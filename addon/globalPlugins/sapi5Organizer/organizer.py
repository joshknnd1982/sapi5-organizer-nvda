# -*- coding: UTF-8 -*-
# SAPI5 Organizer, a global plugin for NVDA.
# Copyright (C) 2026 Josh Kennedy.
# This file is covered by the MIT License.
# See the file LICENSE for more details.

"""Organises the voices of a live SAPI5 driver into engine, language and variant.

The three combo boxes the add-on adds to NVDA's Speech settings are a *view*
over the driver's single ``voice`` setting rather than independent state. Engine,
language and variant are always derived from whichever voice is currently
selected, which means the new controls can never drift out of step with NVDA's
own Voice combo box, no matter which of them the user changes.

The list of selectable voices always comes from the driver itself
(``synth.availableVoices``), never from the registry, because only the driver
knows what its own process can really load. The registry index is used purely to
enrich each of those voices with the engine and language facts that the driver
does not expose.
"""

from collections import OrderedDict

from logHandler import log

from .sapiIndex import LOG_PREFIX, splitTokenId

try:
	import languageHandler
except ImportError:  # pragma: no cover - only happens outside NVDA.
	languageHandler = None

#: Language code used for voices that declare no language at all.
UNKNOWN_LANGUAGE = "unknown"


def _languageDescription(code):
	"""A friendly, localised name for a locale code, falling back to the code."""
	if code == UNKNOWN_LANGUAGE:
		# Translators: Shown in the SAPI language list for voices with no declared language.
		return _("Unknown language")
	if languageHandler is not None:
		try:
			description = languageHandler.getLanguageDescription(code)
			if description:
				return f"{description} ({code})"
		except Exception:
			log.debugWarning(f"{LOG_PREFIX}no description for language {code!r}", exc_info=True)
	return code


def _normalizeLanguage(code):
	if not code:
		return UNKNOWN_LANGUAGE
	return str(code).replace("-", "_")


def _variantLabel(displayName, prefixes):
	"""Shorten a voice's name for the variant list.

	Voice descriptions usually repeat the engine or vendor, for example
	``BestSpeech Alien - English (Classic 1994)`` or ``Nokia Klatt Arabic male``.
	Dropping that prefix leaves a label that is much quicker to listen through,
	since the engine has already been chosen in its own combo box. Prefixes are
	tried longest first, and the full name is kept whenever trimming would leave
	nothing useful behind.
	"""
	name = (displayName or "").strip()
	for prefix in prefixes or ():
		prefix = (prefix or "").strip()
		if not prefix or not name.lower().startswith(prefix.lower()):
			continue
		trimmed = name[len(prefix):].lstrip(" -–—:,")
		if trimmed:
			return trimmed
	return name


class ModelVoice(object):
	"""One selectable voice, placed within an engine and a language."""

	def __init__(self, tokenId, displayName, variantLabel, engineKey, language):
		self.tokenId = tokenId
		self.displayName = displayName
		self.variantLabel = variantLabel
		self.engineKey = engineKey
		self.language = language

	def __repr__(self):
		return f"<ModelVoice {self.variantLabel!r} engine={self.engineKey} lang={self.language}>"


class ModelEngine(object):
	"""One engine, and the languages and voices it offers to the current driver."""

	def __init__(self, key, label):
		self.key = key
		self.label = label
		#: language code -> OrderedDict of token id -> L{ModelVoice}
		self.languages = OrderedDict()

	@property
	def voiceCount(self):
		return sum(len(voices) for voices in self.languages.values())

	def __repr__(self):
		return f"<ModelEngine {self.label!r} languages={len(self.languages)} voices={self.voiceCount}>"


class OrganizerModel(object):
	"""Engine, language and variant choices for one driver's set of voices."""

	def __init__(self):
		#: engine key -> L{ModelEngine}, ordered by engine label.
		self.engines = OrderedDict()
		#: lowercased token id -> L{ModelVoice}
		self.voicesById = {}

	@property
	def isEmpty(self):
		return not self.engines

	def locate(self, voiceId):
		"""Return the L{ModelVoice} for a driver voice id, or None."""
		if not voiceId:
			return None
		return self.voicesById.get(str(voiceId).lower())

	def engineChoices(self):
		"""Ordered mapping of engine key -> label, for the SAPI engine combo box."""
		return OrderedDict((key, engine.label) for key, engine in self.engines.items())

	def languageChoices(self, engineKey):
		"""Ordered mapping of language code -> label, for the chosen engine only."""
		engine = self.engines.get(engineKey)
		if engine is None:
			return OrderedDict()
		return OrderedDict((code, _languageDescription(code)) for code in engine.languages)

	def variantChoices(self, engineKey, language):
		"""Ordered mapping of token id -> label, for the chosen engine and language."""
		engine = self.engines.get(engineKey)
		if engine is None:
			return OrderedDict()
		voices = engine.languages.get(language)
		if not voices:
			return OrderedDict()
		return OrderedDict((voice.tokenId, voice.variantLabel) for voice in voices.values())

	def firstEngine(self):
		for key in self.engines:
			return key
		return ""

	def firstLanguage(self, engineKey):
		engine = self.engines.get(engineKey)
		if engine is None:
			return UNKNOWN_LANGUAGE
		for code in engine.languages:
			return code
		return UNKNOWN_LANGUAGE

	def firstVoiceId(self, engineKey, language):
		engine = self.engines.get(engineKey)
		if engine is None:
			return None
		voices = engine.languages.get(language)
		if not voices:
			return None
		for tokenId in voices:
			return tokenId
		return None

	def findVoiceByVariantLabel(self, engineKey, language, variantLabel):
		"""Find a voice with a given label, used to keep the variant across switches."""
		engine = self.engines.get(engineKey)
		if engine is None or not variantLabel:
			return None
		voices = engine.languages.get(language)
		if not voices:
			return None
		needle = variantLabel.strip().lower()
		for voice in voices.values():
			if voice.variantLabel.strip().lower() == needle:
				return voice.tokenId
		return None

	def describe(self):
		lines = [f"{len(self.voicesById)} voice(s) organised into {len(self.engines)} engine(s)."]
		for engine in self.engines.values():
			languages = ", ".join(
				f"{code} ({len(voices)})" for code, voices in engine.languages.items()
			)
			lines.append(f"  * {engine.label}: {engine.voiceCount} voice(s); {languages}")
		return "\n".join(lines)


def buildModel(availableVoices, index, view):
	"""Organise a driver's voices into engines, languages and variants.

	@param availableVoices: the driver's own ``availableVoices`` mapping of voice
		id to something with ``displayName`` and ``language`` attributes.
	@param index: a L{sapiIndex.SapiIndex} used to enrich each voice.
	@param view: the registry view (32 or 64) matching the driver's bitness,
		which is preferred when a token appears in both views.
	@return: a fully populated L{OrganizerModel}. Never raises.
	"""
	model = OrganizerModel()
	if not availableVoices:
		log.warning(f"{LOG_PREFIX}the driver reported no voices at all; nothing to organise")
		return model
	# engine key -> (label, OrderedDict of language -> list of ModelVoice)
	collected = OrderedDict()
	unresolved = 0
	for voiceId, voiceInfo in availableVoices.items():
		try:
			engine = index.resolveEngine(voiceId, view)
			if engine is not None:
				engineKey = engine.key
				engineLabel = engine.label
				prefixes = engine.labelPrefixes()
			else:
				# Only reachable for a malformed voice id. File it under the
				# container it came from, which is still a real grouping; there
				# is deliberately no bucket of leftovers.
				unresolved += 1
				containerPath, leaf = splitTokenId(voiceId)
				engineKey = (
					f"container:{containerPath.lower()}" if containerPath else f"voice:{str(voiceId).lower()}"
				)
				engineLabel = splitTokenId(containerPath)[1] or containerPath or str(voiceId)
				prefixes = ()
				log.warning(
					f"{LOG_PREFIX}voice {voiceId!r} names no identifiable engine; "
					f"filed under {engineLabel!r}"
				)
			token = index.lookupToken(voiceId, preferredView=view)
			displayName = getattr(voiceInfo, "displayName", None) or (token.name if token else None) or str(voiceId)
			language = _normalizeLanguage(
				getattr(voiceInfo, "language", None) or (token.primaryLanguage if token else None)
			)
			voice = ModelVoice(
				tokenId=voiceId,
				displayName=displayName,
				variantLabel=_variantLabel(displayName, prefixes),
				engineKey=engineKey,
				language=language,
			)
			entry = collected.get(engineKey)
			if entry is None:
				entry = (engineLabel, OrderedDict())
				collected[engineKey] = entry
			entry[1].setdefault(language, []).append(voice)
			model.voicesById[str(voiceId).lower()] = voice
		except Exception:
			log.error(f"{LOG_PREFIX}could not organise voice {voiceId!r}", exc_info=True)
	# Build the final, sorted structure.
	for engineKey, (engineLabel, languages) in sorted(
		collected.items(), key=lambda item: item[1][0].lower()
	):
		modelEngine = ModelEngine(engineKey, engineLabel)
		for language in sorted(languages, key=lambda code: _languageDescription(code).lower()):
			voices = sorted(languages[language], key=lambda voice: voice.variantLabel.lower())
			modelEngine.languages[language] = OrderedDict((voice.tokenId, voice) for voice in voices)
		model.engines[engineKey] = modelEngine
	if unresolved:
		log.warning(
			f"{LOG_PREFIX}{unresolved} voice(s) named no identifiable engine and were filed "
			"under their container; see the log above for their ids"
		)
	log.info(f"{LOG_PREFIX}organised the {view} bit driver's voices. {model.describe()}")
	return model
