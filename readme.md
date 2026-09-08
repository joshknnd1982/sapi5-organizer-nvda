# SAPI5 Organizer

An NVDA add-on that turns one unmanageably long list of SAPI5 voices into three
short, meaningful ones: **SAPI engine**, **SAPI language** and **SAPI voice
variant**.

* Author: Josh Kennedy <joshknnd1982@gmail.com>
* GitHub: <https://github.com/joshknnd1982>
* Requires: NVDA 2026.1 or later, Windows
* Licence: GNU General Public License version 2

## What it does

If you have several SAPI5 engines installed, NVDA's Voice combo box in Speech
settings becomes a single flat list of every voice from every engine, in every
language. On a well stocked machine that can be hundreds of entries.

SAPI5 Organizer indexes every SAPI5 voice on the system and adds three cascading
combo boxes to NVDA's existing **Speech** settings category:

| Combo box | What it lists |
| --- | --- |
| SAPI engine | Every SAPI5 engine that provides at least one voice the current synthesizer can load |
| SAPI language | Only the languages provided by the engine you just chose |
| SAPI voice variant | Only the voices of that engine, in that language |

Choosing an engine immediately narrows the language list, and choosing a
language immediately narrows the variant list. Choosing a variant selects that
voice, and NVDA's own Voice combo box updates to match. It works the other way
round too: change the Voice combo box and the three new combo boxes follow.

The controls appear **only** while the *Microsoft Speech API version 5 (32 bit)*
or *Microsoft Speech API version 5 (64 bit)* synthesizer is selected. With any
other synthesizer, NVDA's Speech settings are exactly as they always were.

## Saving your settings

The three controls are ordinary NVDA driver settings, so they behave like every
other speech setting:

* pressing **OK** or **Apply** in the Settings dialog stores them;
* **NVDA+control+c** saves them permanently, so they survive a restart;
* each **configuration profile** remembers its own selection, so you can make a
  profile per engine or per language if you prefer to work that way.

Settings are stored under `[speech]` in `nvda.ini`, in the `[[sapi5]]` or
`[[sapi5_32]]` section, next to the settings NVDA stores for those drivers
itself.

## The voice index

**NVDA menu → Tools → SAPI5 voice index...** opens a report of the complete
catalogue, which is broader than what any single synthesizer can offer. For each
voice it shows:

* the voice name and the engine that provides it;
* the language or languages the voice declares;
* whether that engine is registered for 32 bit programs, 64 bit programs or
  both, or `not registered` if its COM server is missing;
* the SAPI category (classic `SAPI5` or Windows `OneCore`);
* the registry key the voice comes from.

The list can be filtered as you type, and the whole report can be copied to the
clipboard, saved to a text file, or written to the NVDA log. `not registered` is
worth knowing about: it usually explains a voice that appears in a list but
refuses to speak, because the engine was uninstalled without its voices being
removed.

## Commands

The add-on ships no assigned gestures, so it cannot clash with anything you
already use. To assign one, open **NVDA menu → Preferences → Input gestures**
and look under the **SAPI5 Organizer** category:

* show the SAPI5 voice index;
* rescan the system for SAPI5 engines, languages and voices;
* write the full voice index to the NVDA log;
* report the current SAPI5 engine, language and voice variant.

## How it works

NVDA builds the controls in its Speech settings panel from the current
synthesizer's `supportedSettings`. Rather than create a settings dialog of its
own, this add-on adds three `DriverSetting`s to NVDA's two SAPI5 driver classes,
so NVDA renders, stores and restores them with the same code it uses for Voice,
Rate and Volume. That is why they save with the configuration, follow
configuration profiles, and vanish for other synthesizers without any extra
work.

The catalogue is built by reading the SAPI token registry rather than by talking
to COM. Enumerating voices through `SAPI.SPVoice` only ever reveals voices of
the calling process' own bitness, so a 64 bit NVDA could never see 32 bit only
engines that way, and instantiating an unknown third party engine merely to
interrogate it is slow and can hang. The add-on therefore scans:

* both the **32 bit** and the **64 bit** registry views;
* both `HKEY_LOCAL_MACHINE` and `HKEY_CURRENT_USER`;
* both the `Speech` (classic SAPI5) and `Speech_OneCore` categories.

Voices are grouped by their engine's COM class id, which is the truest identity
an engine has, and labelled with the vendor name that users actually recognise.
When one vendor ships more than one engine, the engine's server file name is
appended so the two can be told apart. Nothing is hard coded: vendors, class
ids, install paths and languages are all read from whatever happens to be
registered, and every one of those fields is treated as optional, because plenty
of third party engines omit them.

The list of *selectable* voices always comes from the running driver itself, not
from the registry, because only the driver knows what its own process can really
load. The registry index is used purely to enrich those voices with the engine
and language facts the driver does not expose.

## Troubleshooting

The add-on logs in detail. Set **NVDA menu → Preferences → Settings → General →
Logging level** to **Debug**, restart NVDA, then open the log with
**NVDA+f1**. Every line the add-on writes is prefixed with `SAPI5 Organizer:`,
including one line per voice token discovered, the engine each voice was grouped
into, and every voice change the three combo boxes make.

For a snapshot of what the add-on can see, use **Write report to the NVDA log**
in the voice index dialog.

## Building from source

An NVDA add-on is a zip archive with a `manifest.ini` at its root, so no build
tooling beyond Python is required:

```bash
python build.py
```

This produces `sapi5Organizer-<version>.nvda-addon`, which can be installed from
**NVDA menu → Tools → Add-on store → Install from external source**, or by
opening the file directly.

## Repository layout

```
addon/
  manifest.ini                     add-on metadata
  doc/en/readme.html               the help shown by NVDA
  globalPlugins/sapi5Organizer/
    __init__.py                    the global plugin: patching, menu, commands
    sapiIndex.py                   registry scan of every SAPI5 voice token
    organizer.py                   groups voices into engine, language, variant
    driverPatch.py                 adds the settings to NVDA's SAPI5 drivers
    indexDialog.py                 the voice index report dialog
build.py                           builds the .nvda-addon package
```

## Licence

Copyright (C) 2026 Josh Kennedy. Released under the GNU General Public License
version 2. See [LICENSE](LICENSE).
