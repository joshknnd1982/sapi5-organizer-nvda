# -*- coding: UTF-8 -*-
# SAPI5 Organizer, a global plugin for NVDA.
# Copyright (C) 2026 Josh Kennedy.
# This file is covered by the GNU General Public License version 2.
# See the file LICENSE for more details.

"""A complete index of every SAPI5 engine, language and voice on the system.

SAPI5 publishes voices in two quite different ways, and an add-on that only
understands the first will miss most of the voices on a well stocked machine:

*Static tokens* live in the registry under ``...\\Voices\\Tokens``. Each one is a
key describing a single voice, naming the engine that provides it through a
``CLSID`` value.

*Token enumerators* live under ``...\\Voices\\TokenEnums``. Each one is a single
key holding nothing but a friendly name and the class id of a COM object, and
that object invents its voices at run time. Those voices have **no registry keys
at all**; they merely have registry style ids of the form
``...\\Voices\\TokenEnums\\<engine>\\<voice>``. No amount of registry scanning
will find them, and on the machine this was developed against they account for
473 of the 725 available voices, across sixteen engines.

The engine behind any voice is therefore resolved from the *container* its id
sits in, rather than from a token key that may not exist:

* a voice under ``TokenEnums\\<engine>\\`` belongs to that enumerator, whose
  friendly name and class id are read from the enumerator's own key;
* a voice under a ``Tokens`` store is grouped by the class id in its own key,
  because one such store holds the voices of many unrelated engines.

Every voice therefore lands in a real, named engine. There is no catch-all
group. Anything unrecognised is still grouped by the container it came from,
which is a genuine grouping rather than a bucket of leftovers.
"""

import ctypes
import locale
import os
import winreg
from collections import OrderedDict

from logHandler import log

#: Prefixed to every log line so the add-on's output is easy to find in the NVDA log.
LOG_PREFIX = "SAPI5 Organizer: "

#: The registry views to scan, mapped to the ``KEY_WOW64_*`` flag that selects them.
#: 64 first so that it wins when a token is registered identically in both views.
VIEWS = OrderedDict(((64, winreg.KEY_WOW64_64KEY), (32, winreg.KEY_WOW64_32KEY)))

#: Hive name (exactly as it appears in a SAPI token id) mapped to its handle.
_HIVES = OrderedDict(
	(
		("HKEY_LOCAL_MACHINE", winreg.HKEY_LOCAL_MACHINE),
		("HKEY_CURRENT_USER", winreg.HKEY_CURRENT_USER),
	)
)

#: Roots holding SAPI voice containers, as (key path below the hive, category label).
#: ``Speech`` is classic SAPI5, ``Speech_OneCore`` holds the modern Windows voices,
#: and ``Speech Server`` is the Microsoft Speech Platform runtime, which many users
#: install for its extra languages. Absent roots are skipped silently.
_VOICE_ROOTS = (
	(r"SOFTWARE\Microsoft\Speech\Voices", "SAPI5"),
	(r"SOFTWARE\Microsoft\Speech_OneCore\Voices", "OneCore"),
	(r"SOFTWARE\Microsoft\Speech Server\v11.0\Voices", "Speech Platform"),
)

#: Child of a voices root holding one key per voice.
_TOKENS_CONTAINER = "Tokens"
#: Child of a voices root holding one key per *engine*, each generating voices at run time.
_ENUMS_CONTAINER = "TokenEnums"

#: Value names under a token key that may hold the engine's COM class id.
_CLSID_VALUES = ("CLSID", "Clsid")


def _readValues(key):
	"""Return every value of an open registry key as a dict, tolerating junk."""
	values = {}
	index = 0
	while True:
		try:
			name, data, _valueType = winreg.EnumValue(key, index)
		except OSError:
			break
		except Exception:
			log.debugWarning(f"{LOG_PREFIX}unreadable registry value at index {index}", exc_info=True)
			index += 1
			continue
		values[name] = data
		index += 1
	return values


def _subKeyNames(key):
	"""Return the names of a key's sub keys, tolerating junk."""
	names = []
	index = 0
	while True:
		try:
			names.append(winreg.EnumKey(key, index))
		except OSError:
			break
		except Exception:
			log.debugWarning(f"{LOG_PREFIX}unreadable sub key at index {index}", exc_info=True)
		index += 1
	return names


def _resolveIndirectString(value):
	"""Resolve an ``@dll,-123`` style indirect string to displayable text."""
	if not value or not isinstance(value, str) or not value.startswith("@"):
		return value
	try:
		buf = ctypes.create_unicode_buffer(1024)
		if ctypes.windll.shlwapi.SHLoadIndirectString(value, buf, 1024, None) == 0 and buf.value:
			return buf.value
	except Exception:
		log.debugWarning(f"{LOG_PREFIX}could not resolve indirect string {value!r}", exc_info=True)
	return value


def _lcidToLocale(lcid):
	"""Convert one hexadecimal SAPI language id (e.g. ``409``) to ``en_US``."""
	try:
		return locale.windows_locale[int(lcid, 16)]
	except (ValueError, KeyError, TypeError):
		return None


def parseLanguages(rawLanguage):
	"""Parse a SAPI ``Language`` attribute into a list of locale names.

	The attribute is a semicolon separated list of hexadecimal language ids, in
	either case, and a voice claiming several is legal and does happen.
	"""
	languages = []
	if not rawLanguage:
		return languages
	for part in str(rawLanguage).split(";"):
		part = part.strip()
		if not part:
			continue
		name = _lcidToLocale(part)
		if name and name not in languages:
			languages.append(name)
	return languages


def splitTokenId(tokenId):
	"""Split a SAPI voice id into (container path, leaf name).

	Voice ids are registry style paths, whether or not a key actually exists at
	that location, so this works for enumerator generated voices too.
	"""
	text = str(tokenId or "").rstrip("\\")
	if "\\" not in text:
		return "", text
	container, _sep, leaf = text.rpartition("\\")
	return container, leaf


def _openPath(fullPath, viewFlag):
	"""Open a registry key named by a full path including its hive name."""
	hiveName, _sep, subPath = str(fullPath).partition("\\")
	hive = _HIVES.get(hiveName.upper())
	if hive is None or not subPath:
		return None
	try:
		return winreg.OpenKey(hive, subPath, 0, winreg.KEY_READ | viewFlag)
	except OSError:
		return None
	except Exception:
		log.debugWarning(f"{LOG_PREFIX}could not open {fullPath}", exc_info=True)
		return None


class VoiceToken(object):
	"""One SAPI5 voice."""

	def __init__(self, tokenId, view, category, name, clsid, languages, attributes, tokenValues, generated=False):
		#: Full registry style path, which is also the id NVDA's SAPI5 drivers use.
		self.tokenId = tokenId
		#: The registry view (32 or 64) this voice was found in.
		self.view = view
		#: "SAPI5", "OneCore", "Speech Platform" or the container's own name.
		self.category = category
		#: Human readable voice name.
		self.name = name
		#: The engine's COM class id, where the voice has a key of its own.
		self.clsid = clsid
		#: Locale names such as ``en_US``. May be empty.
		self.languages = languages
		self.attributes = attributes
		self.tokenValues = tokenValues
		#: True when the voice was produced by a token enumerator rather than
		#: read from a registry key of its own.
		self.generated = generated

	@property
	def vendor(self):
		for key in ("Vendor", "vendor"):
			value = self.attributes.get(key)
			if value:
				return str(value).strip()
		return None

	@property
	def primaryLanguage(self):
		return self.languages[0] if self.languages else None

	def __repr__(self):
		return f"<VoiceToken {self.name!r} view={self.view} clsid={self.clsid} langs={self.languages}>"


class EngineInfo(object):
	"""One discovered SAPI5 engine."""

	#: A group of voices sharing a class id inside a ``Tokens`` store.
	KIND_TOKENS = "tokens"
	#: A ``TokenEnums`` entry: a COM object that generates its voices at run time.
	KIND_ENUMERATOR = "enumerator"

	def __init__(self, key, clsid=None, vendor=None, label=None, kind=KIND_TOKENS, containerPath=None):
		self.key = key
		self.clsid = clsid
		self.vendor = vendor
		#: A name read straight from the registry, which always wins over a guess.
		self.explicitLabel = label
		self.kind = kind
		#: The container these voices' ids sit in, for enumerator engines.
		self.containerPath = containerPath
		#: Registry view -> path of the COM server implementing the engine.
		self.servers = {}
		#: Every voice provided by this engine, keyed by registry view.
		self.tokensByView = {32: [], 64: []}
		self.label = label or vendor or clsid or "?"

	@property
	def views(self):
		return sorted(view for view, path in self.servers.items() if path)

	@property
	def serverPath(self):
		for view in (64, 32):
			if self.servers.get(view):
				return self.servers[view]
		return None

	@property
	def bitnessLabel(self):
		views = self.views
		if not views:
			# Translators: Shown when a SAPI5 engine's COM server is not registered.
			return _("not registered")
		# Translators: Describes which process architectures a SAPI5 engine supports.
		return ", ".join(_("{bits} bit").format(bits=view) for view in views)

	@property
	def voiceCount(self):
		return len({token.tokenId.lower() for tokens in self.tokensByView.values() for token in tokens})

	def addToken(self, token):
		existing = self.tokensByView.setdefault(token.view, [])
		if not any(known.tokenId.lower() == token.tokenId.lower() for known in existing):
			existing.append(token)

	def languageCodes(self):
		languages = OrderedDict()
		for view in (64, 32):
			for token in self.tokensByView.get(view, ()):
				for language in token.languages:
					languages[language] = True
		return list(languages)

	def labelPrefixes(self):
		"""Prefixes worth stripping from a voice name to get a short variant label.

		``Nokia Klatt Voices`` should shorten ``Nokia Klatt Arabic male`` to
		``Arabic male``, so the label with a trailing "Voices" removed is offered
		as well as the label and the vendor themselves.
		"""
		candidates = []
		for text in (self.explicitLabel, self.label, self.vendor):
			if not text:
				continue
			text = text.strip()
			if text and text not in candidates:
				candidates.append(text)
			trimmed = text
			for suffix in (" Voices", " voices"):
				if trimmed.endswith(suffix):
					trimmed = trimmed[: -len(suffix)].strip()
			if trimmed and trimmed not in candidates:
				candidates.append(trimmed)
		# Longest first, so the most specific prefix is removed.
		return sorted(candidates, key=len, reverse=True)

	def __repr__(self):
		return f"<EngineInfo {self.label!r} kind={self.kind} views={self.views} voices={self.voiceCount}>"


def _engineServerPath(clsid, viewFlag):
	"""Return the COM server path registered for C{clsid} in one registry view."""
	if not clsid:
		return None
	for server in ("InprocServer32", "LocalServer32"):
		try:
			with winreg.OpenKey(
				winreg.HKEY_CLASSES_ROOT,
				f"CLSID\\{clsid}\\{server}",
				0,
				winreg.KEY_READ | viewFlag,
			) as key:
				path = winreg.QueryValueEx(key, "")[0]
				if path:
					return str(path)
		except OSError:
			continue
		except Exception:
			log.debugWarning(f"{LOG_PREFIX}error reading COM server for {clsid}", exc_info=True)
	return None


def _tokenEngineKey(clsid, vendor, containerPath):
	"""The grouping key for a voice that has a registry key of its own.

	One ``Tokens`` store holds the voices of many unrelated engines, so they are
	told apart by the class id naming the engine, falling back to the vendor and
	finally to the store itself.
	"""
	if clsid:
		return f"clsid:{clsid.lower()}"
	if vendor:
		return f"vendor:{vendor.strip().lower()}"
	return f"container:{str(containerPath).lower()}"


class SapiIndex(object):
	"""The complete catalogue of SAPI5 engines, languages and voices."""

	def __init__(self):
		#: view -> lowercased voice id -> L{VoiceToken}
		self.tokensByView = {32: OrderedDict(), 64: OrderedDict()}
		#: engine key -> L{EngineInfo}
		self.engines = OrderedDict()
		#: lowercased container path -> engine key, for enumerator containers.
		self._enginesByContainer = {}
		#: Containers that were scanned, recorded for the log and the report.
		self.scannedKeys = []
		#: Resolution results, so repeated look ups cost nothing.
		self._resolutionCache = {}
		#: True once SAPI has been asked for the voices its enumerators generate.
		self.generatedVoicesLoaded = False

	# --- look up ------------------------------------------------------------

	def lookupToken(self, tokenId, preferredView=None):
		"""Find a voice by its id, preferring one registry view but accepting either."""
		if not tokenId:
			return None
		needle = str(tokenId).lower()
		views = []
		if preferredView in self.tokensByView:
			views.append(preferredView)
		views.extend(view for view in (64, 32) if view not in views)
		for view in views:
			token = self.tokensByView[view].get(needle)
			if token is not None:
				return token
		return None

	def resolveEngine(self, tokenId, view=64):
		"""Return the L{EngineInfo} responsible for a voice id.

		Works for any voice the driver can report, including enumerator
		generated voices that have no registry key, and voices in containers
		this build has never heard of. Only returns None for an empty id.
		"""
		if not tokenId:
			return None
		cacheKey = str(tokenId).lower()
		cached = self._resolutionCache.get(cacheKey)
		if cached is not None:
			return self.engines.get(cached)
		engine = self._resolveEngineUncached(tokenId, view)
		if engine is not None:
			self._resolutionCache[cacheKey] = engine.key
		return engine

	def _resolveEngineUncached(self, tokenId, view):
		containerPath, _leaf = splitTokenId(tokenId)
		containerKey = containerPath.lower()
		# A voice generated by a token enumerator, or any other container that is
		# itself a single engine.
		engineKey = self._enginesByContainer.get(containerKey)
		if engineKey is not None:
			return self.engines.get(engineKey)
		# A voice inside a shared token store: group it by the engine named in
		# its own key.
		token = self.lookupToken(tokenId, preferredView=view)
		if token is None and containerPath:
			token = self._readTokenOnDemand(tokenId, view)
		if token is not None:
			key = _tokenEngineKey(token.clsid, token.vendor, containerPath)
			engine = self.engines.get(key)
			if engine is None:
				engine = EngineInfo(key, clsid=token.clsid, vendor=token.vendor, containerPath=containerPath)
				self._registerEngine(engine, assignLabel=True)
			engine.addToken(token)
			return engine
		# Nothing is known about this voice, so group it by the container it came
		# from. That is still a real grouping, never a bucket of leftovers.
		if not containerPath:
			return None
		log.debug(f"{LOG_PREFIX}voice {tokenId!r} is unknown; grouping it by its container")
		return self._engineForContainer(containerPath, view, category=None)

	def _readTokenOnDemand(self, tokenId, view):
		"""Read a single voice key that the scan did not cover."""
		viewFlag = VIEWS.get(view, winreg.KEY_WOW64_64KEY)
		key = _openPath(tokenId, viewFlag)
		if key is None:
			return None
		with key:
			values = _readValues(key)
			try:
				with winreg.OpenKey(key, "Attributes", 0, winreg.KEY_READ | viewFlag) as attrKey:
					attributes = _readValues(attrKey)
			except OSError:
				attributes = {}
		if not values and not attributes:
			return None
		containerPath, leaf = splitTokenId(tokenId)
		token = _makeToken(tokenId, view, _categoryFor(containerPath), values, attributes, leaf)
		self.tokensByView.setdefault(view, OrderedDict())[tokenId.lower()] = token
		log.debug(f"{LOG_PREFIX}read voice {tokenId} on demand")
		return token

	def _engineForContainer(self, containerPath, view, category, label=None, clsid=None):
		"""Return (creating if needed) the engine representing a whole container."""
		containerKey = containerPath.lower()
		knownKey = self._enginesByContainer.get(containerKey)
		if knownKey is not None:
			return self.engines.get(knownKey)
		if label is None or clsid is None:
			viewFlag = VIEWS.get(view, winreg.KEY_WOW64_64KEY)
			readLabel, readClsid = _readContainerIdentity(containerPath, viewFlag)
			label = label or readLabel
			clsid = clsid or readClsid
		# The COM object is an engine's true identity. The same engine is often
		# registered in more than one voice category (a SAPI5 entry and a
		# Speech_OneCore entry pointing at one class id), and listing it twice
		# would be plainly wrong, so the class id is the key wherever there is one.
		key = f"clsid:{clsid.lower()}" if clsid else f"container:{containerKey}"
		engine = self.engines.get(key)
		if engine is None:
			if not label:
				label = splitTokenId(containerPath)[1] or containerPath
			engine = EngineInfo(
				key,
				clsid=clsid,
				vendor=None,
				label=label,
				kind=EngineInfo.KIND_ENUMERATOR,
				containerPath=containerPath,
			)
			self._registerEngine(engine, assignLabel=False)
		elif label and not engine.explicitLabel:
			# A friendly name read from the registry always beats a guess.
			engine.explicitLabel = label
			engine.label = label
		self._enginesByContainer[containerKey] = engine.key
		return engine

	def _registerEngine(self, engine, assignLabel):
		for view, viewFlag in VIEWS.items():
			engine.servers[view] = _engineServerPath(engine.clsid, viewFlag)
		if assignLabel:
			engine.label = _baseLabelFor(engine)
		self.engines[engine.key] = engine
		log.debug(
			f"{LOG_PREFIX}engine {engine.label!r} kind={engine.kind} clsid={engine.clsid} "
			f"servers={engine.servers}"
		)

	# --- reporting ----------------------------------------------------------

	def enginesForView(self, view):
		return [engine for engine in self.engines.values() if engine.tokensByView.get(view)]

	@property
	def totalVoiceCount(self):
		return len({tokenId for tokens in self.tokensByView.values() for tokenId in tokens})

	def sortEngines(self):
		self.engines = OrderedDict(sorted(self.engines.items(), key=lambda item: item[1].label.lower()))

	def describe(self):
		lines = [
			f"{self.totalVoiceCount} voice(s) from {len(self.engines)} engine(s); "
			f"{len(self.tokensByView[64])} known in the 64 bit view, "
			f"{len(self.tokensByView[32])} in the 32 bit view."
		]
		for line in self.scannedKeys:
			lines.append(f"  scanned {line}")
		for engine in self.engines.values():
			languages = engine.languageCodes()
			lines.append(
				f"  * {engine.label}: {engine.voiceCount} voice(s), "
				f"{len(languages)} language(s) [{', '.join(languages) or 'unknown'}], "
				f"{engine.bitnessLabel}, kind={engine.kind}, clsid={engine.clsid}, "
				f"server={engine.serverPath}"
			)
		return "\n".join(lines)


def _categoryFor(containerPath):
	"""A short category name for a container, from the root it belongs to."""
	lowered = str(containerPath).lower()
	for path, category in _VOICE_ROOTS:
		if path.lower() in lowered:
			return category
	return "SAPI5"


def _baseLabelFor(engine):
	if engine.explicitLabel:
		return engine.explicitLabel
	if engine.vendor:
		return engine.vendor
	if engine.serverPath:
		return os.path.splitext(os.path.basename(engine.serverPath))[0]
	if engine.containerPath:
		return splitTokenId(engine.containerPath)[1] or engine.containerPath
	# Translators: Label for a SAPI5 engine whose vendor could not be determined.
	return _("Unknown engine")


def _makeToken(tokenId, view, category, values, attributes, fallbackName):
	name = _resolveIndirectString(values.get("")) or attributes.get("Name") or fallbackName
	clsid = None
	for valueName in _CLSID_VALUES:
		if values.get(valueName):
			clsid = str(values[valueName]).strip()
			break
	return VoiceToken(
		tokenId=tokenId,
		view=view,
		category=category,
		name=str(name),
		clsid=clsid,
		languages=parseLanguages(attributes.get("Language")),
		attributes=attributes,
		tokenValues=values,
	)


def _readContainerIdentity(containerPath, viewFlag):
	"""Read a container key's friendly name and class id, if it has them."""
	key = _openPath(containerPath, viewFlag)
	if key is None:
		return None, None
	with key:
		values = _readValues(key)
	label = _resolveIndirectString(values.get("")) or None
	clsid = None
	for valueName in _CLSID_VALUES:
		if values.get(valueName):
			clsid = str(values[valueName]).strip()
			break
	return label, clsid


def _assignEngineLabels(engines):
	"""Give every engine a unique, human friendly label."""
	byLabel = {}
	for engine in engines.values():
		byLabel.setdefault(_baseLabelFor(engine), []).append(engine)
	for base, sharing in byLabel.items():
		if len(sharing) == 1:
			sharing[0].label = base
			continue
		usedSuffixes = set()
		for engine in sharing:
			suffix = None
			if engine.serverPath:
				suffix = os.path.splitext(os.path.basename(engine.serverPath))[0]
			if not suffix or suffix in usedSuffixes:
				suffix = (engine.clsid or engine.key)[:10]
			usedSuffixes.add(suffix)
			engine.label = f"{base} ({suffix})"


def buildIndex(includeGeneratedVoices=True):
	"""Scan the system and return a fully populated L{SapiIndex}.

	@param includeGeneratedVoices: also ask SAPI itself for the voices that token
		enumerators generate at run time. They have no registry keys, so this is
		the only way to *list* them. It loads the SAPI runtime, so it is left out
		of the path that feeds the Speech settings combo boxes: those only need
		to know which engine a voice belongs to, which is answered from the
		registry alone, and they take their list of voices from the driver.
	Never raises. An add-on that cannot index must still leave NVDA usable.
	"""
	index = SapiIndex()
	try:
		for view, viewFlag in VIEWS.items():
			for hiveName, hive in _HIVES.items():
				for rootPath, category in _VOICE_ROOTS:
					_scanRoot(index, view, viewFlag, hiveName, hive, rootPath, category)
		_assignEngineLabels(index.engines)
		index.sortEngines()
		if includeGeneratedVoices:
			addGeneratedVoices(index)
		log.info(f"{LOG_PREFIX}index built. {index.describe()}")
	except Exception:
		log.error(f"{LOG_PREFIX}failed to build the SAPI5 index", exc_info=True)
	return index


def _scanRoot(index, view, viewFlag, hiveName, hive, rootPath, category):
	"""Scan one ``Voices`` root, covering both its token store and its enumerators."""
	try:
		root = winreg.OpenKey(hive, rootPath, 0, winreg.KEY_READ | viewFlag)
	except OSError:
		log.debug(f"{LOG_PREFIX}no {category} voices at {hiveName}\\{rootPath} ({view} bit view)")
		return
	except Exception:
		log.debugWarning(f"{LOG_PREFIX}could not open {hiveName}\\{rootPath}", exc_info=True)
		return
	with root:
		children = _subKeyNames(root)
	fullRoot = f"{hiveName}\\{rootPath}"
	for child in children:
		if child.lower() == _TOKENS_CONTAINER.lower():
			_scanTokenStore(index, view, viewFlag, f"{fullRoot}\\{child}", category)
		elif child.lower() == _ENUMS_CONTAINER.lower():
			_scanEnumerators(index, view, viewFlag, f"{fullRoot}\\{child}", category)
		else:
			log.debug(f"{LOG_PREFIX}ignoring unexpected key {fullRoot}\\{child}")


def _scanTokenStore(index, view, viewFlag, containerPath, category):
	"""Scan a ``Tokens`` container, one key per voice."""
	key = _openPath(containerPath, viewFlag)
	if key is None:
		return
	with key:
		names = _subKeyNames(key)
		index.scannedKeys.append(f"{containerPath} ({view} bit view): {len(names)} voice(s)")
		log.debug(f"{LOG_PREFIX}scanning {containerPath} ({view} bit view), {len(names)} voice(s)")
		for name in names:
			try:
				_readStoreToken(index, view, viewFlag, containerPath, category, key, name)
			except Exception:
				log.debugWarning(f"{LOG_PREFIX}could not read voice {name!r}", exc_info=True)


def _readStoreToken(index, view, viewFlag, containerPath, category, container, subKeyName):
	tokenId = f"{containerPath}\\{subKeyName}"
	with winreg.OpenKey(container, subKeyName, 0, winreg.KEY_READ | viewFlag) as tokenKey:
		values = _readValues(tokenKey)
		try:
			with winreg.OpenKey(tokenKey, "Attributes", 0, winreg.KEY_READ | viewFlag) as attrKey:
				attributes = _readValues(attrKey)
		except OSError:
			attributes = {}
	token = _makeToken(tokenId, view, category, values, attributes, subKeyName)
	index.tokensByView[view][tokenId.lower()] = token
	engineKey = _tokenEngineKey(token.clsid, token.vendor, containerPath)
	engine = index.engines.get(engineKey)
	if engine is None:
		engine = EngineInfo(engineKey, clsid=token.clsid, vendor=token.vendor, containerPath=containerPath)
		index._registerEngine(engine, assignLabel=True)
	elif not engine.vendor and token.vendor:
		engine.vendor = token.vendor
	engine.addToken(token)
	index._resolutionCache[tokenId.lower()] = engine.key
	log.debug(
		f"{LOG_PREFIX}voice {tokenId} | name={token.name!r} | vendor={token.vendor!r} "
		f"| clsid={token.clsid} | languages={token.languages} | view={view} | category={category}"
	)


def _scanEnumerators(index, view, viewFlag, containerPath, category):
	"""Scan a ``TokenEnums`` container, one key per *engine*.

	These keys hold no voices. Each names a COM object that generates its voices
	when SAPI is asked for them, so the engine is recorded here and its voices
	are attached later, either from SAPI itself or from the running driver.
	"""
	key = _openPath(containerPath, viewFlag)
	if key is None:
		return
	with key:
		names = _subKeyNames(key)
	index.scannedKeys.append(f"{containerPath} ({view} bit view): {len(names)} engine(s)")
	log.debug(f"{LOG_PREFIX}scanning {containerPath} ({view} bit view), {len(names)} engine(s)")
	for name in names:
		enginePath = f"{containerPath}\\{name}"
		try:
			label, clsid = _readContainerIdentity(enginePath, viewFlag)
			engine = index._engineForContainer(enginePath, view, category, label=label, clsid=clsid)
			log.debug(
				f"{LOG_PREFIX}engine enumerator {enginePath} | label={engine.label!r} | clsid={clsid}"
			)
		except Exception:
			log.debugWarning(f"{LOG_PREFIX}could not read enumerator {enginePath}", exc_info=True)


def enumerateSapiVoices():
	"""Ask SAPI for every voice it can offer this process.

	Yields (id, description, raw language attribute). This is the only way to see
	voices produced by token enumerators, because they have no registry keys.
	Returns an empty list rather than raising if SAPI is unavailable.
	"""
	voices = []
	try:
		import comtypes.client

		tts = comtypes.client.CreateObject("SAPI.SPVoice")
		tokens = tts.GetVoices()
		# #2629: iterating uses IEnumVARIANT, whose items do not always expose the
		# right interface, so fetch by index as NVDA's own driver does.
		for position in range(len(tokens)):
			try:
				token = tokens[position]
				try:
					language = token.getattribute("language")
				except Exception:
					language = None
				voices.append((token.Id, token.GetDescription(), language))
			except Exception:
				log.debugWarning(f"{LOG_PREFIX}could not read SAPI voice {position}", exc_info=True)
	except Exception:
		log.warning(
			f"{LOG_PREFIX}SAPI could not be asked for its voices; "
			"voices generated by token enumerators will be missing from the index",
			exc_info=True,
		)
	return voices


def addGeneratedVoices(index, view=None):
	"""Attach the voices SAPI generates at run time to their engines.

	Safe to call more than once; voices already known are skipped. Labels are
	reassigned afterwards, because an engine that had no voices before may now
	need telling apart from another of the same name.
	"""
	added = _addGeneratedVoices(index, view)
	if added:
		_assignEngineLabels(index.engines)
		index.sortEngines()
	index.generatedVoicesLoaded = True
	return added


def _addGeneratedVoices(index, view=None):
	"""Attach the voices SAPI generates at run time to their engines."""
	if view is None:
		# SAPI answers for the calling process, which is NVDA's own bitness.
		view = 64 if ctypes.sizeof(ctypes.c_void_p) == 8 else 32
	added = 0
	for tokenId, description, rawLanguage in enumerateSapiVoices():
		try:
			if index.lookupToken(tokenId, preferredView=view) is not None:
				continue
			containerPath, leaf = splitTokenId(tokenId)
			token = VoiceToken(
				tokenId=tokenId,
				view=view,
				category=_categoryFor(containerPath),
				name=str(description or leaf),
				clsid=None,
				languages=parseLanguages(rawLanguage),
				attributes={},
				tokenValues={},
				generated=True,
			)
			index.tokensByView.setdefault(view, OrderedDict())[tokenId.lower()] = token
			engine = index.resolveEngine(tokenId, view=view)
			if engine is not None:
				engine.addToken(token)
			added += 1
			log.debug(
				f"{LOG_PREFIX}generated voice {tokenId} | name={token.name!r} "
				f"| languages={token.languages} | engine={engine.label if engine else None!r}"
			)
		except Exception:
			log.debugWarning(f"{LOG_PREFIX}could not add generated voice {tokenId!r}", exc_info=True)
	if added:
		log.info(f"{LOG_PREFIX}added {added} voice(s) generated by token enumerators")
	return added
