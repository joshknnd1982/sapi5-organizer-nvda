#!/usr/bin/env python3
# -*- coding: UTF-8 -*-
"""Build sapi5Organizer into an installable .nvda-addon package.

An NVDA add-on is simply a zip archive whose root contains ``manifest.ini``, so
no build tooling is needed beyond the standard library. Run ``python build.py``
from the repository root.
"""

import os
import re
import sys
import zipfile

ADDON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "addon")

#: Never shipped inside the add-on.
EXCLUDED_DIRS = {"__pycache__", ".git", ".svn"}
EXCLUDED_SUFFIXES = (".pyc", ".pyo", ".orig", ".rej", ".bak")


def readManifestField(field):
	"""Read a single top level field out of the add-on manifest."""
	manifestPath = os.path.join(ADDON_DIR, "manifest.ini")
	with open(manifestPath, "r", encoding="utf-8") as manifest:
		for line in manifest:
			match = re.match(r"""^\s*%s\s*=\s*["']?(.*?)["']?\s*$""" % field, line)
			if match:
				return match.group(1)
	raise KeyError(f"{field} is missing from {manifestPath}")


def collectFiles():
	"""Yield (absolute path, path inside the archive) for everything to package."""
	for root, dirs, files in os.walk(ADDON_DIR):
		dirs[:] = sorted(d for d in dirs if d not in EXCLUDED_DIRS)
		for name in sorted(files):
			if name.endswith(EXCLUDED_SUFFIXES):
				continue
			absolute = os.path.join(root, name)
			yield absolute, os.path.relpath(absolute, ADDON_DIR).replace(os.sep, "/")


def build():
	name = readManifestField("name")
	version = readManifestField("version")
	target = os.path.join(os.path.dirname(ADDON_DIR), f"{name}-{version}.nvda-addon")
	if os.path.exists(target):
		os.remove(target)
	count = 0
	with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
		for absolute, arcname in collectFiles():
			archive.write(absolute, arcname)
			count += 1
	print(f"Built {target}")
	print(f"{count} file(s), {os.path.getsize(target)} bytes")
	return target


if __name__ == "__main__":
	try:
		build()
	except Exception as error:
		print(f"Build failed: {error}", file=sys.stderr)
		raise
