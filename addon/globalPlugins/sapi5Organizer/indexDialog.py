# -*- coding: UTF-8 -*-
# SAPI5 Organizer, a global plugin for NVDA.
# Copyright (C) 2026 Josh Kennedy.
# This file is covered by the MIT License.
# See the file LICENSE for more details.

"""A browsable report of every SAPI5 engine, language and voice on the system.

Unlike the combo boxes in the Speech settings, which can only offer what the
running synthesizer is able to load, this report shows the complete catalogue:
both registry views, both SAPI categories, and engines whose COM server is
missing entirely, which is usually the reason a voice mysteriously refuses to
speak.
"""

import os

import wx

import api
import gui
from gui import guiHelper
from logHandler import log

from .organizer import _languageDescription
from .sapiIndex import LOG_PREFIX

#: Column headings for the report list.
_COLUMNS = (
	# Translators: Column heading in the SAPI5 voice index.
	(_("Voice"), 260),
	# Translators: Column heading in the SAPI5 voice index.
	(_("Engine"), 170),
	# Translators: Column heading in the SAPI5 voice index.
	(_("Language"), 170),
	# Translators: Column heading in the SAPI5 voice index, showing 32 or 64 bit availability.
	(_("Availability"), 130),
	# Translators: Column heading in the SAPI5 voice index (SAPI5 or OneCore).
	(_("Category"), 90),
	# Translators: Column heading in the SAPI5 voice index, the registry key of the voice.
	(_("Registry token"), 420),
)


def buildRows(index):
	"""Flatten the index into one row per voice, sorted for presentation."""
	rows = []
	for engine in index.engines.values():
		merged = {}
		for view in (64, 32):
			for token in engine.tokensByView.get(view, ()):
				entry = merged.setdefault(token.tokenId.lower(), [token, set()])
				entry[1].add(view)
		for token, views in merged.values():
			languages = "; ".join(_languageDescription(code) for code in token.languages) or _(
				# Translators: Shown in the index when a voice declares no language.
				"Unknown language"
			)
			rows.append(
				(
					token.name,
					engine.label,
					languages,
					engine.bitnessLabel,
					token.category,
					token.tokenId,
				)
			)
	rows.sort(key=lambda row: (row[1].lower(), row[2].lower(), row[0].lower()))
	return rows


def buildTextReport(index):
	"""A plain text version of the whole index, for the log, clipboard or a file."""
	lines = [
		# Translators: Title of the SAPI5 index report.
		_("SAPI5 Organizer voice index"),
		"",
		index.describe(),
		"",
	]
	for row in buildRows(index):
		lines.append(
			f"{row[1]} | {row[2]} | {row[0]} | {row[3]} | {row[4]} | {row[5]}"
		)
	return "\n".join(lines)


class Sapi5IndexDialog(wx.Dialog):
	"""Shows the complete SAPI5 catalogue, with a filter and export options."""

	_instance = None

	def __init__(self, parent, index, onRefresh):
		# Translators: Title of the SAPI5 voice index dialog.
		super().__init__(parent, title=_("SAPI5 voice index"), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
		self._index = index
		self._onRefresh = onRefresh
		self._rows = []
		mainSizer = wx.BoxSizer(wx.VERTICAL)
		contents = guiHelper.BoxSizerHelper(self, orientation=wx.VERTICAL)

		self.summary = contents.addItem(wx.StaticText(self, label=""))

		# Translators: Label of the filter field in the SAPI5 voice index.
		self.filterCtrl = contents.addLabeledControl(_("&Filter:"), wx.TextCtrl)
		self.filterCtrl.Bind(wx.EVT_TEXT, self.onFilter)

		self.listCtrl = contents.addItem(
			wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL, size=(900, 380)),
			flag=wx.EXPAND,
			proportion=1,
		)
		for position, (label, width) in enumerate(_COLUMNS):
			self.listCtrl.InsertColumn(position, label, width=width)

		buttons = guiHelper.ButtonHelper(wx.HORIZONTAL)
		# Translators: A button in the SAPI5 voice index dialog.
		refresh = buttons.addButton(self, label=_("&Rescan the system"))
		refresh.Bind(wx.EVT_BUTTON, self.onRefresh)
		# Translators: A button in the SAPI5 voice index dialog.
		copyButton = buttons.addButton(self, label=_("&Copy report to clipboard"))
		copyButton.Bind(wx.EVT_BUTTON, self.onCopy)
		# Translators: A button in the SAPI5 voice index dialog.
		saveButton = buttons.addButton(self, label=_("&Save report..."))
		saveButton.Bind(wx.EVT_BUTTON, self.onSave)
		# Translators: A button in the SAPI5 voice index dialog.
		logButton = buttons.addButton(self, label=_("Write report to the NVDA &log"))
		logButton.Bind(wx.EVT_BUTTON, self.onLog)
		contents.addItem(buttons)

		contents.addDialogDismissButtons(wx.Button(self, wx.ID_CLOSE))
		self.Bind(wx.EVT_BUTTON, self.onClose, id=wx.ID_CLOSE)
		self.EscapeId = wx.ID_CLOSE

		mainSizer.Add(contents.sizer, border=guiHelper.BORDER_FOR_DIALOGS, flag=wx.ALL | wx.EXPAND, proportion=1)
		mainSizer.Fit(self)
		self.SetSizer(mainSizer)
		self.populate()
		self.CentreOnScreen()
		self.filterCtrl.SetFocus()

	def populate(self):
		"""Reload the list from the index, honouring the current filter."""
		self._rows = buildRows(self._index)
		self.summary.SetLabel(
			# Translators: Summary line in the SAPI5 voice index. {voices} and {engines} are counts.
			_("{voices} voice(s) from {engines} engine(s) found on this system.").format(
				voices=self._index.totalVoiceCount, engines=len(self._index.engines)
			)
		)
		self.applyFilter()

	def applyFilter(self):
		needle = self.filterCtrl.GetValue().strip().lower()
		self.listCtrl.DeleteAllItems()
		shown = 0
		for row in self._rows:
			if needle and not any(needle in str(cell).lower() for cell in row):
				continue
			position = self.listCtrl.InsertItem(shown, row[0])
			for column in range(1, len(_COLUMNS)):
				self.listCtrl.SetItem(position, column, str(row[column]))
			shown += 1
		if shown:
			self.listCtrl.Select(0)
			self.listCtrl.Focus(0)
		log.debug(f"{LOG_PREFIX}index dialog showing {shown} of {len(self._rows)} voice(s)")

	def onFilter(self, evt):
		self.applyFilter()

	def onRefresh(self, evt):
		try:
			self._index = self._onRefresh()
			self.populate()
			# Translators: Reported after rescanning the system for SAPI5 voices.
			ui_message(_("Rescan complete."))
		except Exception:
			log.error(f"{LOG_PREFIX}could not rescan", exc_info=True)

	def onCopy(self, evt):
		try:
			api.copyToClip(buildTextReport(self._index))
			# Translators: Reported when the SAPI5 report has been copied.
			ui_message(_("Report copied to the clipboard."))
		except Exception:
			log.error(f"{LOG_PREFIX}could not copy the report", exc_info=True)

	def onLog(self, evt):
		log.info(f"{LOG_PREFIX}full index report follows.\n{buildTextReport(self._index)}")
		# Translators: Reported when the SAPI5 report has been written to the NVDA log.
		ui_message(_("Report written to the NVDA log."))

	def onSave(self, evt):
		with wx.FileDialog(
			self,
			# Translators: Title of the dialog for saving the SAPI5 report.
			message=_("Save the SAPI5 voice index"),
			defaultFile="sapi5-voice-index.txt",
			wildcard=_("Text file") + " (*.txt)|*.txt",
			style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
		) as fileDialog:
			if fileDialog.ShowModal() != wx.ID_OK:
				return
			path = fileDialog.GetPath()
		try:
			with open(path, "w", encoding="utf-8") as report:
				report.write(buildTextReport(self._index))
			log.info(f"{LOG_PREFIX}report saved to {path}")
			# Translators: Reported after the SAPI5 report has been saved. {name} is a file name.
			ui_message(_("Report saved to {name}.").format(name=os.path.basename(path)))
		except Exception:
			log.error(f"{LOG_PREFIX}could not save the report to {path!r}", exc_info=True)
			gui.messageBox(
				# Translators: Shown when the SAPI5 report could not be saved.
				_("The report could not be saved. See the NVDA log for details."),
				# Translators: Title of an error dialog.
				_("Error"),
				wx.OK | wx.ICON_ERROR,
				self,
			)

	def onClose(self, evt):
		self.Destroy()
		Sapi5IndexDialog._instance = None


def ui_message(message):
	"""Speak and braille a short message, without importing ui at module scope."""
	try:
		import ui

		ui.message(message)
	except Exception:
		log.debugWarning(f"{LOG_PREFIX}could not announce {message!r}", exc_info=True)


def showIndexDialog(index, onRefresh):
	"""Open the index dialog, or bring the existing one to the front."""
	if Sapi5IndexDialog._instance is not None:
		try:
			Sapi5IndexDialog._instance.Raise()
			return
		except RuntimeError:
			# The window was destroyed behind our back.
			Sapi5IndexDialog._instance = None
	dialog = Sapi5IndexDialog(gui.mainFrame, index, onRefresh)
	Sapi5IndexDialog._instance = dialog
	gui.mainFrame.prePopup()
	dialog.Show()
	gui.mainFrame.postPopup()
