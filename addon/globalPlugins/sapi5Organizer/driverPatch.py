# -*- coding: UTF-8 -*-
# SAPI5 Organizer, a global plugin for NVDA.
# Copyright (C) 2026 Josh Kennedy.
# This file is covered by the MIT License.
# See the file LICENSE for more details.

"""Adds SAPI engine, language and voice variant settings to NVDA's SAPI5 drivers.

Rather than build a settings dialog of its own, the add-on teaches the two SAPI5
drivers about three extra L{DriverSetting}s. NVDA's Speech settings panel builds
its controls from ``synth.supportedSettings``, so the three combo boxes appear in
the standard Speech category the moment a SAPI5 synthesizer is selected, and are
absent for every other synthesizer, because only these two driver classes are
touched. Everything else follows for free:

* values are written to ``config.conf["speech"][<driver>]`` when the user presses
  OK or Apply, because that is what NVDA does with any driver setting;
* they are saved with the rest of the configuration by NVDA+control+c, because
  L{AutoSettings} registers ``saveSettings`` with ``config.pre_configSave``;
* they are per configuration profile, because the configuration itself is.

The two drivers need slightly different treatment. ``sapi5`` is an ordinary
driver whose ``supportedSettings`` is a plain tuple, while ``sapi5_32`` is a
proxy for a driver living in a 32 bit host process, and fetches its settings over
that bridge through a ``_get_supportedSettings`` property. Both shapes are
detected at run time rather than assumed.
"""

from collections import OrderedDict

import config
from autoSettingsUtils.driverSetting import DriverSetting
from autoSettingsUtils.utils import StringParameterInfo
from logHandler import log

from . import organizer, sapiIndex
from .sapiIndex import LOG_PREFIX

#: The ids of the settings this add-on adds. Deliberately single lower case
#: words: NVDA derives the name of the property holding a setting's choices with
#: ``"available%ss" % id.capitalize()``, and ``str.capitalize`` lower cases
#: everything after the first character, so mixed case ids are a trap.
ENGINE_SETTING = "sapiengine"
LANGUAGE_SETTING = "sapilanguage"
VARIANT_SETTING = "sapivariant"
SETTING_IDS = (ENGINE_SETTING, LANGUAGE_SETTING, VARIANT_SETTING)

#: The NVDA synthesizer drivers this add-on extends, and the registry view whose
#: voices each of them can actually load.
SAPI5_DRIVERS = {"sapi5": 64, "sapi5_32": 32}

#: Marks a class that has already been patched, so that a reload cannot double up.
_PATCH_FLAG = "_sapi5OrganizerPatched"

_index = None
_settings = None


def getIndex(refresh=False):
	"""The shared index, built on first use.

	Registry only, so it is cheap enough for NVDA's start up. That is all the
	Speech settings combo boxes need: the voices themselves come from the driver,
	and this only has to say which engine each of them belongs to.
	"""
	global _index
	if _index is None or refresh:
		log.debug(f"{LOG_PREFIX}building the registry index (refresh={refresh})")
		_index = sapiIndex.buildIndex(includeGeneratedVoices=False)
	return _index


def getFullIndex(refresh=False):
	"""The complete catalogue, for the voice index report.

	Additionally asks SAPI for the voices its token enumerators generate, which
	have no registry keys and so cannot be found any other way. Done on demand
	rather than at start up, because it loads the SAPI runtime.
	"""
	index = getIndex(refresh=refresh)
	if not getattr(index, "generatedVoicesLoaded", False):
		log.debug(f"{LOG_PREFIX}asking SAPI for the voices its token enumerators generate")
		sapiIndex.addGeneratedVoices(index)
	return index


def makeSettings():
	"""The three L{DriverSetting}s this add-on contributes.

	The objects are built once and reused. The 32 bit driver fetches its settings
	over a bridge every time ``supportedSettings`` is read, and the Speech panel
	reads that repeatedly, so returning fresh objects each time would be pure
	waste. Nothing mutates a L{DriverSetting}, so sharing them is safe.

	No ``defaultVal`` is given, exactly as NVDA's own voice setting does, so the
	generated configuration spec matches a shape NVDA is already known to accept.
	"""
	global _settings
	if _settings is None:
		_settings = (
			# Translators: Label of a combo box in NVDA's Speech settings, listing SAPI5 engines.
			DriverSetting(ENGINE_SETTING, _("SAPI &engine"), availableInSettingsRing=True),
			# Translators: Label of a combo box in NVDA's Speech settings, listing SAPI5 languages.
			DriverSetting(LANGUAGE_SETTING, _("SAPI lan&guage"), availableInSettingsRing=True),
			# Translators: Label of a combo box in NVDA's Speech settings, listing SAPI5 voices.
			DriverSetting(VARIANT_SETTING, _("SAPI voice va&riant"), availableInSettingsRing=True),
		)
	return _settings


def _withOrganizerSettings(baseSettings):
	"""Return C{baseSettings} with our three settings inserted before the voice.

	Placing them ahead of Voice makes the Speech panel read as a drill down,
	engine then language then variant then the full voice list. It also means
	NVDA restores the stored ``voice`` last when loading the configuration, so
	the voice always has the final say.
	"""
	settings = list(baseSettings or ())
	existing = {getattr(setting, "id", None) for setting in settings}
	additions = [setting for setting in makeSettings() if setting.id not in existing]
	if not additions:
		return tuple(settings)
	position = next(
		(index for index, setting in enumerate(settings) if getattr(setting, "id", None) == "voice"),
		len(settings),
	)
	return tuple(settings[:position] + additions + settings[position:])


def _viewForDriver(driver):
	"""The registry view whose voices this driver can load."""
	return SAPI5_DRIVERS.get(getattr(driver, "name", None), 64)


def _isLoading(driver):
	"""True while NVDA is restoring settings from the configuration.

	During a restore the three settings must not touch the voice. NVDA applies
	the stored ``voice`` itself, and it is the authority; letting engine,
	language and variant each also assign a voice would re-initialise the
	speech engine several times for no benefit.
	"""
	return bool(getattr(driver, "_sapi5OrganizerLoading", False))


def _model(driver):
	"""The organised view of this driver's voices, cached on the driver."""
	try:
		voices = driver.availableVoices or OrderedDict()
	except Exception:
		log.error(f"{LOG_PREFIX}could not read availableVoices from the driver", exc_info=True)
		voices = OrderedDict()
	cached = getattr(driver, "_sapi5OrganizerModel", None)
	if cached is not None and getattr(driver, "_sapi5OrganizerModelSize", None) == len(voices):
		return cached
	model = organizer.buildModel(voices, getIndex(), _viewForDriver(driver))
	driver._sapi5OrganizerModel = model
	driver._sapi5OrganizerModelSize = len(voices)
	return model


def invalidateModel(driver):
	"""Force the organised view to be rebuilt the next time it is needed."""
	for attribute in ("_sapi5OrganizerModel", "_sapi5OrganizerModelSize"):
		try:
			delattr(driver, attribute)
		except AttributeError:
			pass


def _currentVoice(driver):
	try:
		return driver.voice
	except Exception:
		log.error(f"{LOG_PREFIX}could not read the current voice from the driver", exc_info=True)
		return None


def _currentModelVoice(driver):
	return _model(driver).locate(_currentVoice(driver))


def _asChoices(mapping):
	"""Convert an id -> label mapping into what NVDA's combo boxes expect."""
	return OrderedDict((key, StringParameterInfo(key, label)) for key, label in mapping.items())


def _applyVoice(driver, tokenId, reason):
	"""Select a voice on the driver, unless it is already the current one."""
	if not tokenId:
		log.debugWarning(f"{LOG_PREFIX}{reason}: no matching voice was found")
		return
	current = _currentVoice(driver)
	if current and str(current).lower() == str(tokenId).lower():
		log.debug(f"{LOG_PREFIX}{reason}: already using {tokenId}")
		return
	log.debug(f"{LOG_PREFIX}{reason}: selecting voice {tokenId}")
	try:
		driver.voice = tokenId
	except Exception:
		log.error(f"{LOG_PREFIX}could not select voice {tokenId!r}", exc_info=True)


# --- The settings themselves -------------------------------------------------


def _get_sapiengine(driver):
	model = _model(driver)
	voice = model.locate(_currentVoice(driver))
	if voice is not None:
		return voice.engineKey
	return model.firstEngine()


def _set_sapiengine(driver, value):
	model = _model(driver)
	if value not in model.engines:
		log.debug(f"{LOG_PREFIX}ignoring unknown SAPI engine {value!r}")
		return
	if _isLoading(driver):
		log.debug(f"{LOG_PREFIX}restoring engine {value!r}; the stored voice will be applied instead")
		return
	engine = model.engines[value]
	currentVoice = model.locate(_currentVoice(driver))
	# Stay in the same language, and on a like named voice, where the newly
	# chosen engine offers them.
	language = currentVoice.language if currentVoice else None
	if language not in engine.languages:
		language = model.firstLanguage(value)
	tokenId = None
	if currentVoice is not None:
		tokenId = model.findVoiceByVariantLabel(value, language, currentVoice.variantLabel)
	_applyVoice(driver, tokenId or model.firstVoiceId(value, language), f"SAPI engine set to {value!r}")


def _get_availableSapiengines(driver):
	return _asChoices(_model(driver).engineChoices())


def _get_sapilanguage(driver):
	model = _model(driver)
	voice = model.locate(_currentVoice(driver))
	if voice is not None:
		return voice.language
	return model.firstLanguage(_get_sapiengine(driver))


def _set_sapilanguage(driver, value):
	model = _model(driver)
	engineKey = _get_sapiengine(driver)
	engine = model.engines.get(engineKey)
	if engine is None or value not in engine.languages:
		log.debug(f"{LOG_PREFIX}ignoring unknown SAPI language {value!r} for engine {engineKey!r}")
		return
	if _isLoading(driver):
		log.debug(f"{LOG_PREFIX}restoring language {value!r}; the stored voice will be applied instead")
		return
	currentVoice = model.locate(_currentVoice(driver))
	tokenId = None
	if currentVoice is not None:
		tokenId = model.findVoiceByVariantLabel(engineKey, value, currentVoice.variantLabel)
	_applyVoice(driver, tokenId or model.firstVoiceId(engineKey, value), f"SAPI language set to {value!r}")


def _get_availableSapilanguages(driver):
	"""Only the languages of the currently selected engine."""
	model = _model(driver)
	return _asChoices(model.languageChoices(_get_sapiengine(driver)))


def _get_sapivariant(driver):
	model = _model(driver)
	voice = model.locate(_currentVoice(driver))
	if voice is not None:
		return voice.tokenId
	return model.firstVoiceId(_get_sapiengine(driver), _get_sapilanguage(driver)) or ""


def _set_sapivariant(driver, value):
	model = _model(driver)
	if model.locate(value) is None:
		log.debug(f"{LOG_PREFIX}ignoring unknown SAPI voice variant {value!r}")
		return
	if _isLoading(driver):
		log.debug(f"{LOG_PREFIX}restoring variant {value!r}; the stored voice will be applied instead")
		return
	_applyVoice(driver, value, f"SAPI voice variant set to {value!r}")


def _get_availableSapivariants(driver):
	"""Only the voices of the currently selected engine and language."""
	model = _model(driver)
	return _asChoices(model.variantChoices(_get_sapiengine(driver), _get_sapilanguage(driver)))


#: setting id -> (getter, setter, choices getter)
_ACCESSORS = {
	ENGINE_SETTING: (_get_sapiengine, _set_sapiengine, _get_availableSapiengines),
	LANGUAGE_SETTING: (_get_sapilanguage, _set_sapilanguage, _get_availableSapilanguages),
	VARIANT_SETTING: (_get_sapivariant, _set_sapivariant, _get_availableSapivariants),
}


def _choicesPropertyName(settingId):
	"""The property NVDA looks for to populate a setting's combo box."""
	return "available%ss" % settingId.capitalize()


# --- Patching ----------------------------------------------------------------


#: Distinguishes "the class had no attribute of its own" from "it was None".
_MISSING = object()


def _restoreClassAttribute(cls, name, previous):
	"""Put a class attribute back exactly as it was before patching."""
	if previous is _MISSING:
		# The class had none of its own; remove ours so whatever a base class
		# provides becomes visible again.
		try:
			delattr(cls, name)
		except AttributeError:
			pass
	else:
		setattr(cls, name, previous)


def _patchSupportedSettings(cls):
	"""Extend a driver class' ``supportedSettings``, whatever shape it has.

	Returns a callable that undoes the change. The attribute may be defined on
	this class or inherited from a base, so what was there is recorded exactly,
	rather than assumed.
	"""
	ownSupported = cls.__dict__.get("supportedSettings", _MISSING)
	holder = None
	for klass in cls.__mro__:
		if "supportedSettings" in klass.__dict__:
			holder = klass.__dict__["supportedSettings"]
			break
	if isinstance(holder, (tuple, list)):
		cls.supportedSettings = _withOrganizerSettings(tuple(holder))
		log.debug(f"{LOG_PREFIX}{cls.__name__}: extended a static supportedSettings tuple")
		return lambda: _restoreClassAttribute(cls, "supportedSettings", ownSupported)
	# A dynamic property, as used by the 32 bit proxy driver, whose settings come
	# over the bridge from the host process. Wrap the underlying getter so the
	# remote list is still consulted every time.
	originalGetter = getattr(cls, "_get_supportedSettings", None)
	if originalGetter is None:
		log.error(f"{LOG_PREFIX}{cls.__name__}: no supportedSettings to extend; leaving it alone")
		return lambda: None
	ownGetter = cls.__dict__.get("_get_supportedSettings", _MISSING)

	def _patchedGetter(self):
		# Never mutate the remote list; the proxy caches it.
		return _withOrganizerSettings(originalGetter(self))

	cls._get_supportedSettings = _patchedGetter
	cls.supportedSettings = property(_patchedGetter)
	log.debug(f"{LOG_PREFIX}{cls.__name__}: wrapped a dynamic supportedSettings property")

	def undo():
		_restoreClassAttribute(cls, "supportedSettings", ownSupported)
		_restoreClassAttribute(cls, "_get_supportedSettings", ownGetter)

	return undo


def _patchLoadSuppression(cls):
	"""Flag the driver while NVDA restores settings, and return an undo callable."""
	undos = []
	for methodName in ("initSettings", "loadSettings"):
		original = getattr(cls, methodName, None)
		if original is None:
			continue
		wasOwn = methodName in cls.__dict__

		def makeWrapper(original):
			def wrapper(self, *args, **kwargs):
				previous = getattr(self, "_sapi5OrganizerLoading", False)
				self._sapi5OrganizerLoading = True
				try:
					return original(self, *args, **kwargs)
				finally:
					self._sapi5OrganizerLoading = previous
			return wrapper

		setattr(cls, methodName, makeWrapper(original))

		def makeUndo(methodName=methodName, original=original, wasOwn=wasOwn):
			def undo():
				if wasOwn:
					setattr(cls, methodName, original)
				else:
					try:
						delattr(cls, methodName)
					except AttributeError:
						pass
			return undo

		undos.append(makeUndo())
	return lambda: [undo() for undo in undos]


def patchDriverClass(cls):
	"""Teach one SAPI5 driver class about the organizer's settings."""
	if getattr(cls, _PATCH_FLAG, False):
		log.debug(f"{LOG_PREFIX}{cls.__name__} is already patched")
		return False
	undos = [_patchSupportedSettings(cls)]
	added = []
	for settingId, (getter, setter, choices) in _ACCESSORS.items():
		choicesName = _choicesPropertyName(settingId)
		# Install both the NVDA style accessor and an explicit property. The
		# property is what actually matters: NVDA generates properties from
		# ``_get_``/``_set_`` methods when a class is created, which has long
		# since happened for these classes.
		setattr(cls, f"_get_{settingId}", getter)
		setattr(cls, f"_set_{settingId}", setter)
		setattr(cls, f"_get_{choicesName}", choices)
		setattr(cls, settingId, property(getter, setter))
		setattr(cls, choicesName, property(choices))
		added.extend([f"_get_{settingId}", f"_set_{settingId}", f"_get_{choicesName}", settingId, choicesName])
	undos.append(_patchLoadSuppression(cls))

	def undoAttributes():
		for name in added:
			try:
				delattr(cls, name)
			except AttributeError:
				pass

	undos.append(undoAttributes)
	cls._sapi5OrganizerUndo = undos
	setattr(cls, _PATCH_FLAG, True)
	log.info(f"{LOG_PREFIX}patched synthesizer driver class {cls.__module__}.{cls.__name__}")
	return True


def unpatchDriverClass(cls):
	"""Undo L{patchDriverClass}, leaving NVDA exactly as it was."""
	if not getattr(cls, _PATCH_FLAG, False):
		return False
	for undo in reversed(getattr(cls, "_sapi5OrganizerUndo", [])):
		try:
			undo()
		except Exception:
			log.error(f"{LOG_PREFIX}could not fully unpatch {cls.__name__}", exc_info=True)
	for name in (_PATCH_FLAG, "_sapi5OrganizerUndo"):
		try:
			delattr(cls, name)
		except AttributeError:
			pass
	log.info(f"{LOG_PREFIX}removed the patch from {cls.__module__}.{cls.__name__}")
	return True


def patchAllDrivers():
	"""Patch every SAPI5 driver class NVDA offers on this system."""
	patched = []
	for driverName in SAPI5_DRIVERS:
		cls = _importDriverClass(driverName)
		if cls is None:
			continue
		if patchDriverClass(cls):
			patched.append(driverName)
	if not patched:
		log.warning(f"{LOG_PREFIX}no SAPI5 driver classes could be patched")
	return patched


def unpatchAllDrivers():
	for driverName in SAPI5_DRIVERS:
		cls = _importDriverClass(driverName, importIfMissing=False)
		if cls is not None:
			unpatchDriverClass(cls)


def _importDriverClass(driverName, importIfMissing=True):
	"""Import one of NVDA's SAPI5 driver modules and return its SynthDriver class."""
	import sys

	moduleName = f"synthDrivers.{driverName}"
	module = sys.modules.get(moduleName)
	if module is None:
		if not importIfMissing:
			return None
		try:
			import importlib

			module = importlib.import_module(moduleName)
		except Exception:
			# Entirely expected on an NVDA without the 32 bit bridge, or a
			# system where SAPI5 is unavailable.
			log.debug(f"{LOG_PREFIX}could not import {moduleName}", exc_info=True)
			return None
	cls = getattr(module, "SynthDriver", None)
	if cls is None:
		log.warning(f"{LOG_PREFIX}{moduleName} has no SynthDriver class")
	return cls


# --- The live synthesizer ----------------------------------------------------


def applyToLiveSynth(synth):
	"""Bring an already running SAPI5 synthesizer up to date with the patch.

	NVDA starts speech before it loads global plugins, so the SAPI5 driver in use
	at start up was created before its class gained the new settings. Its
	configuration spec therefore has to be extended and the stored values loaded
	by hand, once.
	"""
	name = getattr(synth, "name", None)
	if name not in SAPI5_DRIVERS:
		return False
	try:
		settings = [
			setting for setting in synth.supportedSettings if getattr(setting, "id", None) in SETTING_IDS
		]
	except Exception:
		log.error(f"{LOG_PREFIX}could not read supportedSettings from the live synthesizer", exc_info=True)
		return False
	if not settings:
		log.warning(f"{LOG_PREFIX}the live synthesizer {name!r} does not expose the new settings")
		return False
	invalidateModel(synth)
	try:
		spec = synth._getConfigSpecForSettings(settings)
		speechSpec = config.conf.spec["speech"]
		if name in speechSpec:
			speechSpec[name].update(spec)
		else:
			speechSpec[name] = spec
		log.debug(f"{LOG_PREFIX}extended the configuration spec for {name!r}")
	except Exception:
		log.error(f"{LOG_PREFIX}could not extend the configuration spec for {name!r}", exc_info=True)
	try:
		# The voice NVDA already restored is authoritative, so suppress the
		# setters while the stored engine, language and variant are read back.
		synth._sapi5OrganizerLoading = True
		try:
			synth._loadSpecificSettings(synth, settings)
		finally:
			synth._sapi5OrganizerLoading = False
		log.debug(f"{LOG_PREFIX}loaded stored organizer settings for {name!r}")
	except Exception:
		log.error(f"{LOG_PREFIX}could not load stored organizer settings for {name!r}", exc_info=True)
	_refreshSettingsRing()
	try:
		log.info(f"{LOG_PREFIX}live synthesizer {name!r}: {_model(synth).describe()}")
	except Exception:
		log.error(f"{LOG_PREFIX}could not summarise the live synthesizer", exc_info=True)
	return True


def _refreshSettingsRing():
	"""Rebuild NVDA's synth settings ring so the new settings appear in it."""
	try:
		import globalVars

		ring = getattr(globalVars, "settingsRing", None)
		update = getattr(ring, "updateSupportedSettings", None)
		if callable(update):
			update()
			log.debug(f"{LOG_PREFIX}refreshed the synth settings ring")
	except Exception:
		log.debugWarning(f"{LOG_PREFIX}could not refresh the synth settings ring", exc_info=True)


# --- The Speech settings panel ----------------------------------------------


def _refreshChoiceControl(container, driver, settingId):
	"""Replace the contents of one of our combo boxes from the driver.

	NVDA's own refresh cannot do this. ``AutoSettingsMixin._updateValueForControl``
	changes a combo box's *selection* only, and it looks the new selection up in
	``container._<id>s``, the list of options captured once when the control was
	first built. It never calls ``SetItems``. That is fine for NVDA's own string
	settings, whose contents never change, but the entire point of these three is
	that each one narrows the next, so the items have to be replaced by hand.

	``container._<id>s`` is rewritten alongside the control, because NVDA maps the
	user's next selection back to a value by indexing that list; leaving it stale
	would silently select the wrong voice.
	"""
	combo = getattr(container, f"{settingId}List", None)
	if combo is None:
		log.debug(f"{LOG_PREFIX}no {settingId} control to refresh")
		return False
	try:
		options = list(getattr(driver, _choicesPropertyName(settingId)).values())
	except Exception:
		log.error(f"{LOG_PREFIX}could not read the choices for {settingId}", exc_info=True)
		return False
	try:
		setattr(container, f"_{settingId}s", options)
		combo.SetItems([option.displayName for option in options])
		identifiers = [option.id for option in options]
		current = getattr(driver, settingId, None)
		if current in identifiers:
			combo.SetSelection(identifiers.index(current))
		elif options:
			combo.SetSelection(0)
		log.debug(
			f"{LOG_PREFIX}refreshed the {settingId} combo box: {len(options)} item(s), "
			f"showing {current!r}"
		)
		return True
	except Exception:
		log.error(f"{LOG_PREFIX}could not refresh the {settingId} combo box", exc_info=True)
		return False


def patchSettingsDialog():
	"""Make the three combo boxes narrow one another in the Speech panel.

	Two things are missing from NVDA for this to work. It only refreshes the
	panel at all when the *voice* changes, and even then the refresh cannot
	change what a combo box contains. Both are handled here, and only for this
	add-on's settings on a SAPI5 driver, so every other synthesizer and setting
	behaves exactly as it always did.
	"""
	try:
		import gui.settingsDialogs as settingsDialogs
	except Exception:
		log.error(f"{LOG_PREFIX}could not import NVDA's settings dialogs", exc_info=True)
		return False
	changer = getattr(settingsDialogs, "StringDriverSettingChanger", None)
	if changer is None:
		log.error(f"{LOG_PREFIX}NVDA has no StringDriverSettingChanger; cascading is unavailable")
		return False
	if getattr(changer, "_sapi5OrganizerPatched", False):
		return False
	original = changer.__call__

	def patchedCall(self, evt):
		original(self, evt)
		try:
			settingId = getattr(getattr(self, "setting", None), "id", None)
			# A voice chosen from NVDA's own Voice combo box may belong to a
			# different engine, so that has to narrow the three lists as well.
			if settingId not in SETTING_IDS and settingId != "voice":
				return
			driver = getattr(self, "driver", None)
			if getattr(driver, "name", None) not in SAPI5_DRIVERS:
				return
			container = getattr(self, "container", None)
			if container is None:
				return
			log.debug(f"{LOG_PREFIX}refreshing the Speech panel after {settingId} changed")
			if settingId != "voice":
				# NVDA refreshes the panel itself when the voice changes, but for
				# any other setting it does nothing at all.
				update = getattr(container, "updateDriverSettings", None)
				if callable(update):
					update(changedSetting=settingId)
			# NVDA's refresh only moves selections about, so replace the contents
			# of every list this change narrows.
			for dependent in SETTING_IDS:
				if dependent != settingId:
					_refreshChoiceControl(container, driver, dependent)
		except Exception:
			log.error(f"{LOG_PREFIX}could not refresh the Speech panel", exc_info=True)

	changer.__call__ = patchedCall
	changer._sapi5OrganizerOriginalCall = original
	changer._sapi5OrganizerPatched = True
	log.debug(f"{LOG_PREFIX}patched StringDriverSettingChanger for cascading combo boxes")
	return True


def unpatchSettingsDialog():
	try:
		import gui.settingsDialogs as settingsDialogs
	except Exception:
		return
	changer = getattr(settingsDialogs, "StringDriverSettingChanger", None)
	if changer is None or not getattr(changer, "_sapi5OrganizerPatched", False):
		return
	changer.__call__ = changer._sapi5OrganizerOriginalCall
	del changer._sapi5OrganizerOriginalCall
	del changer._sapi5OrganizerPatched
	log.debug(f"{LOG_PREFIX}restored StringDriverSettingChanger")
