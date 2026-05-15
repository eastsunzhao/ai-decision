"""Web frontend package for the local nanobot chat UI.

The ``web`` package is the thin integration layer between:

- Flask routes in :mod:`web.app`
- per-request execution adapters in :mod:`web.run_bot`
- session workspace bootstrapping in :mod:`web.session_workspace`
- small UI-facing normalization helpers in :mod:`web.composer_prefs`
  and :mod:`web.workbench`

Most of the actual agent behavior still lives in the vendored ``nanobot``
package. The code here is mainly about adapting that runtime to a browser UI.
"""
