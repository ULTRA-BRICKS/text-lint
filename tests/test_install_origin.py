"""install.sh pulls and installs whatever its clone's origin serves, so it must recognise the real
repo by HOST and PATH — never by substring — before trusting a clone. These cases run the origin
functions of the real install.sh in bash, isolated from the rest of the installer."""

import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

INSTALL_SH = Path(__file__).resolve().parent.parent / "install.sh"
_BASH = shutil.which("bash")


def _origin_functions():
    text = INSTALL_SH.read_text(encoding="utf-8")
    repo = re.search(r'^REPO="([^"]+)"$', text, re.M)
    block = re.search(r"^_url_host_path\(\) \{.*?^_origin_is_canonical\(\) \{.*?^\}$", text, re.M | re.S)
    assert repo and block, "install.sh: REPO= or the _url_host_path.._origin_is_canonical block moved"
    return repo.group(1), block.group(0)


def _is_canonical(url):
    repo, block = _origin_functions()
    script = f'REPO={shlex.quote(repo)}\n{block}\n_origin_is_canonical "$1" && echo yes || echo no\n'
    out = subprocess.run([_BASH, "-c", script, "_", url], capture_output=True, text=True, timeout=30, check=True)
    return out.stdout.strip() == "yes"


CANONICAL = [
    "https://git.hyperide.ai/ultrabricks/{r}.git",
    "https://git.hyperide.ai/ultrabricks/{r}",
    "https://git.hyperide.ai/ultrabricks/{r}/",
    "https://alex@git.hyperide.ai/ultrabricks/{r}.git",
    "https://git.hyperide.ai:443/ultrabricks/{r}.git",
    "git@git.hyperide.ai:ultrabricks/{r}.git",
    "ssh://git@git.hyperide.ai:2222/ultrabricks/{r}.git",
]
FOREIGN = [
    "https://git.hyperide.ai.evil.example/ultrabricks/{r}.git",
    "https://git.hyperide.ai@evil.example/ultrabricks/{r}.git",
    "https://evil.example\\@git.hyperide.ai/ultrabricks/{r}.git",
    "https://evil.example/git.hyperide.ai/ultrabricks/{r}.git",
    "https://git.hyperide.ai/ultrabricks/{r}-evil.git",
    "https://git.hyperide.ai/other/{r}.git",
    "https://git.hyperide.ai/ultrabricks/../evil/{r}.git",
    "https://git.hyperide.ai/ultrabricks%2f..%2fevil/{r}.git",
    "http://git.hyperide.ai/ultrabricks/{r}.git",
    "git://git.hyperide.ai/ultrabricks/{r}.git",
    "/home/u/src/{r}",
]


@pytest.mark.skipif(_BASH is None, reason="needs bash")
@pytest.mark.parametrize("url,want", [(u, True) for u in CANONICAL] + [(u, False) for u in FOREIGN])
def test_install_sh_recognises_the_repo_by_host_and_path(url, want):
    repo, _ = _origin_functions()
    assert _is_canonical(url.format(r=repo)) is want


def test_install_sh_checks_the_origin_before_pulling_a_clone():
    text = INSTALL_SH.read_text(encoding="utf-8")
    check = text.find('if ! _origin_is_canonical "$actual_url"; then')
    pull = text.find('git -C "$CLONE_DIR" pull')
    assert check != -1, "install.sh no longer checks the existing clone's origin"
    assert pull != -1 and check < pull, "install.sh pulls before checking the clone's origin"
