"""Where the running copy keeps its own code.

Three layouts are legitimate and all must work:

  * the working project — sources live in ``Development/Linux_Support``, with
    the Extension in ``Development/Phase9_JSON_IPC/extension``;
  * the GitHub repository checkout — sources live in ``src/linux-support``,
    with the Extension as a sibling in ``src/extension``;
  * the standalone installer package — sources live in ``ovls/`` next to
    ``install.sh``, so the folder handed to users stays readable.

Every path is derived from this file's own location, so the modules never
hard-code either layout.
"""

from pathlib import Path

CODE_DIR = Path(__file__).resolve().parent
ROOT = CODE_DIR.parent if CODE_DIR.name == "ovls" else CODE_DIR.parents[1]
CODE_TREE = CODE_DIR.relative_to(ROOT)
EXTENSION_TREE = (
    CODE_TREE / "extension"
    if (CODE_DIR / "extension").is_dir()
    else (CODE_DIR.parent.relative_to(ROOT) / "extension"
          if (CODE_DIR.parent / "extension").is_dir()
          else Path("Development") / "Phase9_JSON_IPC" / "extension")
)
OVLS_PY = CODE_TREE / "ovls.py"

# Layout of the standalone package produced by scripts/build_package.py.
PACKAGE_CODE_TREE = Path("ovls")
PACKAGE_EXTENSION_TREE = PACKAGE_CODE_TREE / "extension"
