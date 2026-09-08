# -*- coding: UTF-8 -*-
# SAPI5 Organizer, a global plugin for NVDA.
# Copyright (C) 2026 Josh Kennedy.
# This file is covered by the GNU General Public License version 2.
# See the file LICENSE for more details.

"""A complete, registry driven index of every SAPI5 voice token on the system.

This module deliberately never touches COM. Enumerating voices through
``SAPI.SPVoice`` only ever reveals the voices of the *calling* process' bitness,
so a 64 bit NVDA could never see 32 bit only engines that way, and instantiating
an unknown third party engine merely to interrogate it is slow and can hang.

Every SAPI5 voice is instead described by a registry token, so scanning the
token keys in both the 32 and the 64 bit registry views gives the whole picture
cheaply and safely. NVDA itself uses the same trick: ``synthDrivers.sapi5_32``
decides whether the 32 bit driver is usable by opening ``sapi.spVoice`` under
``KEY_WOW64_32KEY``.

Nothing here is specific to any one machine. Vendors, engine class ids, install
paths and languages are all read from whatever happens to be registered, and
every field is optional, because plenty of third party engines omit them.
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

#: Token container keys to scan, as (key path below the hive, category label).
#: ``Speech`` is classic SAPI5. ``Speech_OneCore`` holds the modern Windows
#: voices; they are not offered by SAPI5 on every system, but they are genuine
#: SAPI tokens and users frequently graft them into the SAPI5 category, so they
#: belong in a complete index.
_CATEGORIES = (
	(r"SOFTWARE\Microsoft\Speech\Voices\Tokens", "SAPI5"),
	(r"SOFTWARE\Microsoft\Speech_OneCore\Voices\Tokens", "OneCore"),
)

#: Value names under a token key that may hold the engine's COM class id.
_CLSID_VALUES = ("CLSID", "Clsid")


def _readValues(key):
	"""Return every value of an open registry key as a dict, tolerating junk.

	Third party engines have been known to store malformed values in their token
	keys, so a single bad value must never abort the scan.
	"""
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


def _resolveIndirectString(value):
	"""Resolve an ``@dll,-123`` style indirect string to displayable text.

	Microsoft's own voice tokens often store their description as a resource
	reference rather than literal text. Returns C{value} unchanged when it is
	not an indirect string, or cannot be resolved.
	"""
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


def _parseLanguages(rawLanguage):
	"""Parse a SAPI ``Language`` attribute into a list of locale names.

	The attribute is a semicolon separated list of hexadecimal LCIDs. A voice
	claiming several languages is unusual but perfectly legal.
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


class VoiceToken(object):
	"""One SAPI5 voice, exactly as the registry describes it."""

	def __init__(self, tokenId, view, category, name, clsid, languages, attributes, tokenValues):
		#: Full registry path, which is also the id NVDA's SAPI5 drivers use for the voice.
		self.tokenId = tokenId
		#: The registry view (32 or 64) this token was found in.
		self.view = view
		#: "SAPI5" or "OneCore".
		self.category = category
		#: Human readable voice name.
		self.name = name
		#: The engine's COM class id, or None when the token omits it.
		self.clsid = clsid
		#: Locale names such as ``en_US``. May be empty.
		self.languages = languages
		#: The raw contents of the token's ``Attributes`` subkey.
		self.attributes = attributes
		#: The raw values of the token key itself.
		self.tokenValues = tokenValues

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
	"""One discovered SAPI5 engine, that is, a COM server that provides voices."""

	def __init__(self, key, clsid, vendor):
		#: Stable identity used for grouping and for the saved configuration.
		self.key = key
		self.clsid = clsid
		self.vendor = vendor
		#: Registry view -> path of the COM server implementing the engine.
		self.servers = {}
		#: Every token provided by this engine, keyed by registry view.
		self.tokensByView = {32: [], 64: []}
		#: Display label. Assigned once the whole index is known, so that
		#: engines sharing a vendor name can still be told apart.
		self.label = vendor or clsid or "?"

	@property
	def views(self):
		"""The registry views in which this engine's COM server is registered."""
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
			# The tokens exist but no COM server is registered for them, so the
			# engine is almost certainly broken or half uninstalled. Worth
			# reporting rather than hiding, so the user can clean it up.
			# Translators: Shown when a SAPI5 engine's COM server is not registered.
			return _("not registered")
		# Translators: Describes which process architectures a SAPI5 engine supports.
		return ", ".join(_("{bits} bit").format(bits=view) for view in views)

	@property
	def voiceCount(self):
		return len({token.tokenId.lower() for tokens in self.tokensByView.values() for token in tokens})

	def languageCodes(self):
		"""Every language offered by this engine, in discovery order."""
		languages = OrderedDict()
		for view in (64, 32):
			for token in self.tokensByView.get(view, ()):
				for language in token.languages:
					languages[language] = True
		return list(languages)

	def __repr__(self):
		return f"<EngineInfo {self.label!r} clsid={self.clsid} views={self.views} voices={self.voiceCount}>"


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


def _engineKeyFor(token):
	"""The stable grouping key for the engine behind a token.

	The COM class id is the truest identity of an engine, but a few tokens omit
	it, so fall back to the vendor and finally to the token's own category, so
	that such voices still group somewhere sensible rather than all landing in a
	single bucket together.
	"""
	if token.clsid:
		return f"clsid:{token.clsid.lower()}"
	vendor = token.vendor
	if vendor:
		return f"vendor:{vendor.lower()}"
	return f"category:{token.category.lower()}"


def _assignEngineLabels(engines):
	"""Give every engine a unique, human friendly label.

	The vendor name is preferred, because that is what users recognise. When two
	genuinely different engines share a vendor (a vendor shipping more than one
	product), the engine's COM server file name is appended so they can still be
	told apart, and if even that clashes, the class id is used.
	"""
	byLabel = {}
	for engine in engines.values():
		base = engine.vendor
		if not base and engine.serverPath:
			base = os.path.splitext(os.path.basename(engine.serverPath))[0]
		if not base:
			# Translators: Label for a SAPI5 engine whose vendor could not be determined.
			base = _("Unknown engine")
		byLabel.setdefault(base, []).append(engine)
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


class SapiIndex(object):
	"""The complete catalogue of SAPI5 engines, languages and voices."""

	def __init__(self):
		#: view -> lowercased token id -> L{VoiceToken}
		self.tokensByView = {32: OrderedDict(), 64: OrderedDict()}
		#: engine key -> L{EngineInfo}
		self.engines = OrderedDict()
		#: Container keys that were scanned, recorded for the log and the report.
		self.scannedKeys = []

	def lookupToken(self, tokenId, preferredView=None):
		"""Find a token by its id, preferring one registry view but accepting either."""
		if not tokenId:
			return None
		needle = tokenId.lower()
		views = []
		if preferredView in self.tokensByView:
			views.append(preferredView)
		views.extend(view for view in (64, 32) if view not in views)
		for view in views:
			token = self.tokensByView[view].get(needle)
			if token is not None:
				return token
		return None

	def engineForToken(self, token):
		if token is None:
			return None
		return self.engines.get(_engineKeyFor(token))

	def enginesForView(self, view):
		"""Engines that provide at least one voice in the given registry view."""
		return [engine for engine in self.engines.values() if engine.tokensByView.get(view)]

	@property
	def totalVoiceCount(self):
		return len({tokenId for tokens in self.tokensByView.values() for tokenId in tokens})

	def describe(self):
		"""A multi line, human readable summary, used for the log and the report."""
		lines = [
			f"{self.totalVoiceCount} voice(s) from {len(self.engines)} engine(s); "
			f"{len(self.tokensByView[64])} token(s) in the 64 bit view, "
			f"{len(self.tokensByView[32])} in the 32 bit view."
		]
		for line in self.scannedKeys:
			lines.append(f"  scanned {line}")
		for engine in self.engines.values():
			languages = engine.languageCodes()
			lines.append(
				f"  * {engine.label}: {engine.voiceCount} voice(s), "
				f"{len(languages)} language(s) [{', '.join(languages) or 'unknown'}], "
				f"{engine.bitnessLabel}, clsid={engine.clsid}, server={engine.serverPath}"
			)
		return "\n".join(lines)


def buildIndex():
	"""Scan the registry and return a fully populated L{SapiIndex}.

	Never raises. An add-on that cannot index must still leave NVDA usable.
	"""
	index = SapiIndex()
	try:
		for view, viewFlag in VIEWS.items():
			for hiveName, hive in _HIVES.items():
				for keyPath, category in _CATEGORIES:
					_scanContainer(index, view, viewFlag, hiveName, hive, keyPath, category)
		_resolveEngineServers(index)
		_assignEngineLabels(index.engines)
		# Order engines by label so that every list the user sees is predictable.
		index.engines = OrderedDict(sorted(index.engines.items(), key=lambda item: item[1].label.lower()))
		log.info(f"{LOG_PREFIX}index built. {index.describe()}")
	except Exception:
		log.error(f"{LOG_PREFIX}failed to build the SAPI5 index", exc_info=True)
	return index


def _scanContainer(index, view, viewFlag, hiveName, hive, keyPath, category):
	"""Scan one token container key in one registry view."""
	try:
		container = winreg.OpenKey(hive, keyPath, 0, winreg.KEY_READ | viewFlag)
	except OSError:
		# Perfectly normal. Most systems lack several of these keys.
		log.debug(f"{LOG_PREFIX}no {category} tokens at {hiveName}\\{keyPath} ({view} bit view)")
		return
	except Exception:
		log.debugWarning(f"{LOG_PREFIX}could not open {hiveName}\\{keyPath}", exc_info=True)
		return
	with container:
		try:
			subKeyCount = winreg.QueryInfoKey(container)[0]
		except Exception:
			log.debugWarning(f"{LOG_PREFIX}could not enumerate {hiveName}\\{keyPath}", exc_info=True)
			return
		index.scannedKeys.append(f"{hiveName}\\{keyPath} ({view} bit view): {subKeyCount} token(s)")
		log.debug(f"{LOG_PREFIX}scanning {hiveName}\\{keyPath} ({view} bit view), {subKeyCount} token(s)")
		for position in range(subKeyCount):
			try:
				subKeyName = winreg.EnumKey(container, position)
			except OSError:
				break
			except Exception:
				log.debugWarning(f"{LOG_PREFIX}could not read token at index {position}", exc_info=True)
				continue
			try:
				_readToken(index, view, viewFlag, hiveName, keyPath, category, container, subKeyName)
			except Exception:
				log.debugWarning(f"{LOG_PREFIX}could not read token {subKeyName!r}", exc_info=True)


def _readToken(index, view, viewFlag, hiveName, keyPath, category, container, subKeyName):
	"""Read a single voice token and add it to the index."""
	tokenId = f"{hiveName}\\{keyPath}\\{subKeyName}"
	with winreg.OpenKey(container, subKeyName, 0, winreg.KEY_READ | viewFlag) as tokenKey:
		tokenValues = _readValues(tokenKey)
		try:
			with winreg.OpenKey(tokenKey, "Attributes", 0, winreg.KEY_READ | viewFlag) as attrKey:
				attributes = _readValues(attrKey)
		except OSError:
			attributes = {}
	name = _resolveIndirectString(tokenValues.get("")) or attributes.get("Name") or subKeyName
	clsid = None
	for valueName in _CLSID_VALUES:
		if tokenValues.get(valueName):
			clsid = str(tokenValues[valueName]).strip()
			break
	languages = _parseLanguages(attributes.get("Language"))
	token = VoiceToken(
		tokenId=tokenId,
		view=view,
		category=category,
		name=str(name),
		clsid=clsid,
		languages=languages,
		attributes=attributes,
		tokenValues=tokenValues,
	)
	index.tokensByView[view][tokenId.lower()] = token
	engineKey = _engineKeyFor(token)
	engine = index.engines.get(engineKey)
	if engine is None:
		engine = EngineInfo(engineKey, clsid, token.vendor)
		index.engines[engineKey] = engine
	elif not engine.vendor and token.vendor:
		engine.vendor = token.vendor
	engine.tokensByView[view].append(token)
	log.debug(
		f"{LOG_PREFIX}token {tokenId} | name={token.name!r} | vendor={token.vendor!r} "
		f"| clsid={clsid} | languages={languages} | view={view} | category={category}"
	)


def _resolveEngineServers(index):
	"""Work out, per registry view, which engines actually have a COM server."""
	for engine in index.engines.values():
		for view, viewFlag in VIEWS.items():
			engine.servers[view] = _engineServerPath(engine.clsid, viewFlag)
		log.debug(
			f"{LOG_PREFIX}engine vendor={engine.vendor!r} clsid={engine.clsid} "
			f"servers={engine.servers} voices={engine.voiceCount}"
		)
