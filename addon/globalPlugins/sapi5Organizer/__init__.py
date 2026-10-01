# -*- coding: UTF-8 -*-
# SAPI5 Organizer, a global plugin for NVDA.
# Copyright (C) 2026 Josh Kennedy.
# This file is covered by the MIT License.
# See the file LICENSE for more details.

"""SAPI5 Organizer: engine, language and voice variant combo boxes for SAPI5.

The plugin indexes every SAPI5 voice registered on the system, in both the 32
and the 64 bit registry views, then extends NVDA's ``sapi5`` and ``sapi5_32``
drivers with three extra settings. NVDA renders those settings itself, in its
own Speech settings panel, so they appear only while one of the two SAPI5
synthesizers is selected and every other synthesizer is left completely alone.
"""

import globalPluginHandler
import gui
import wx
from logHandler import log
from scriptHandler import script

import addonHandler
import synthDriverHandler

from . import driverPatch
from .indexDialog import buildTextReport, showIndexDialog
from .sapiIndex import LOG_PREFIX

try:
	addonHandler.initTranslation()
except Exception:
	log.debugWarning(f"{LOG_PREFIX}no translation catalogue for this add-on", exc_info=True)


def _addonSummary():
	try:
		return addonHandler.getCodeAddon().manifest["summary"]
	except Exception:
		return "SAPI5 Organizer"


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
	scriptCategory = _addonSummary()

	def __init__(self):
		super().__init__()
		self._menuItem = None
		self._synthChangedRegistered = False
		log.info(f"{LOG_PREFIX}starting up")
		self._patch()
		self._registerSynthChanged()
		self._createMenuItem()

	# --- start up and shut down ---------------------------------------------

	def _patch(self):
		try:
			patched = driverPatch.patchAllDrivers()
			log.info(f"{LOG_PREFIX}patched drivers: {', '.join(patched) if patched else 'none'}")
		except Exception:
			log.error(f"{LOG_PREFIX}could not patch the SAPI5 drivers", exc_info=True)
		try:
			driverPatch.patchSettingsDialog()
		except Exception:
			log.error(f"{LOG_PREFIX}could not patch the Speech settings panel", exc_info=True)
		try:
			self._applyToCurrentSynth()
		except Exception:
			log.error(f"{LOG_PREFIX}could not update the running synthesizer", exc_info=True)

	def _applyToCurrentSynth(self):
		"""Bring the synthesizer that NVDA already started into line with the patch."""
		synth = synthDriverHandler.getSynth()
		name = getattr(synth, "name", None)
		if name not in driverPatch.SAPI5_DRIVERS:
			log.info(
				f"{LOG_PREFIX}the current synthesizer is {name!r}, which is not a SAPI5 driver; "
				"NVDA's Speech settings are left untouched"
			)
			return
		log.info(f"{LOG_PREFIX}the current synthesizer is {name!r}; adding the organizer settings")
		driverPatch.applyToLiveSynth(synth)

	def _registerSynthChanged(self):
		try:
			synthDriverHandler.synthChanged.register(self._onSynthChanged)
			self._synthChangedRegistered = True
		except Exception:
			log.error(f"{LOG_PREFIX}could not watch for synthesizer changes", exc_info=True)

	def _onSynthChanged(self, synth=None, **kwargs):
		"""Keep the cached organisation in step when the user switches synthesizer."""
		try:
			name = getattr(synth, "name", None)
			if name not in driverPatch.SAPI5_DRIVERS:
				log.debug(f"{LOG_PREFIX}switched to {name!r}; the organizer settings do not apply")
				return
			log.info(f"{LOG_PREFIX}switched to the SAPI5 driver {name!r}")
			# A driver constructed after the patch registers its own configuration
			# spec and loads its own stored values, so only the cached view of its
			# voices needs discarding.
			driverPatch.invalidateModel(synth)
			log.debug(f"{LOG_PREFIX}{name}: {driverPatch._model(synth).describe()}")
		except Exception:
			log.error(f"{LOG_PREFIX}error handling a synthesizer change", exc_info=True)

	def _createMenuItem(self):
		try:
			self._toolsMenu = gui.mainFrame.sysTrayIcon.toolsMenu
			self._menuItem = self._toolsMenu.Append(
				wx.ID_ANY,
				# Translators: An item in NVDA's Tools menu.
				_("SAPI5 voice &index..."),
				# Translators: The help text for an item in NVDA's Tools menu.
				_("Shows every SAPI5 engine, language and voice found on this system"),
			)
			gui.mainFrame.sysTrayIcon.Bind(wx.EVT_MENU, self._onMenuItem, self._menuItem)
		except Exception:
			log.error(f"{LOG_PREFIX}could not add the Tools menu item", exc_info=True)
			self._menuItem = None

	def terminate(self):
		log.debug(f"{LOG_PREFIX}shutting down")
		if self._menuItem is not None:
			try:
				self._toolsMenu.Remove(self._menuItem)
			except Exception:
				log.debugWarning(f"{LOG_PREFIX}could not remove the Tools menu item", exc_info=True)
			self._menuItem = None
		if self._synthChangedRegistered:
			try:
				synthDriverHandler.synthChanged.unregister(self._onSynthChanged)
			except Exception:
				log.debugWarning(f"{LOG_PREFIX}could not stop watching for synth changes", exc_info=True)
		try:
			driverPatch.unpatchSettingsDialog()
			driverPatch.unpatchAllDrivers()
			# The settings ring holds references to the settings that have just
			# been removed, so it has to be rebuilt from the restored driver.
			driverPatch._refreshSettingsRing()
		except Exception:
			log.error(f"{LOG_PREFIX}could not fully restore NVDA", exc_info=True)
		super().terminate()

	# --- scripts -------------------------------------------------------------

	def _onMenuItem(self, evt):
		wx.CallAfter(self._showIndex)

	def _showIndex(self):
		try:
			showIndexDialog(driverPatch.getFullIndex(), lambda: driverPatch.getFullIndex(refresh=True))
		except Exception:
			log.error(f"{LOG_PREFIX}could not open the index dialog", exc_info=True)

	@script(
		# Translators: Describes a command.
		description=_("Shows the SAPI5 voice index, listing every engine, language and voice found"),
		gesture=None,
	)
	def script_showIndex(self, gesture):
		self._showIndex()

	@script(
		# Translators: Describes a command.
		description=_("Rescans the system for SAPI5 engines, languages and voices"),
		gesture=None,
	)
	def script_rescan(self, gesture):
		import ui

		index = driverPatch.getFullIndex(refresh=True)
		synth = synthDriverHandler.getSynth()
		if getattr(synth, "name", None) in driverPatch.SAPI5_DRIVERS:
			driverPatch.invalidateModel(synth)
		ui.message(
			# Translators: Reported after rescanning. {voices} and {engines} are counts.
			_("{voices} voices from {engines} engines.").format(
				voices=index.totalVoiceCount, engines=len(index.engines)
			)
		)

	@script(
		# Translators: Describes a command.
		description=_("Writes the full SAPI5 voice index to the NVDA log"),
		gesture=None,
	)
	def script_logIndex(self, gesture):
		import ui

		log.info(f"{LOG_PREFIX}full index report follows.\n{buildTextReport(driverPatch.getFullIndex())}")
		# Translators: Reported when the report has been written to the NVDA log.
		ui.message(_("SAPI5 voice index written to the NVDA log."))

	@script(
		# Translators: Describes a command.
		description=_("Reports the current SAPI5 engine, language and voice variant"),
		gesture=None,
	)
	def script_reportCurrent(self, gesture):
		import ui

		synth = synthDriverHandler.getSynth()
		name = getattr(synth, "name", None)
		if name not in driverPatch.SAPI5_DRIVERS:
			ui.message(
				# Translators: Reported when the current synthesizer is not SAPI5.
				_("The current synthesizer is not SAPI5.")
			)
			return
		try:
			model = driverPatch._model(synth)
			engineKey = synth.sapiengine
			engine = model.engines.get(engineKey)
			language = model.languageChoices(engineKey).get(synth.sapilanguage, synth.sapilanguage)
			variant = model.variantChoices(engineKey, synth.sapilanguage).get(synth.sapivariant, "")
			ui.message(
				# Translators: Reports the current SAPI5 selection.
				_("Engine {engine}, language {language}, variant {variant}").format(
					engine=engine.label if engine else engineKey,
					language=language,
					variant=variant,
				)
			)
		except Exception:
			log.error(f"{LOG_PREFIX}could not report the current selection", exc_info=True)
