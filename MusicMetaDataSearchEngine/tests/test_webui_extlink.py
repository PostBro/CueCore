"""Anbieter-Links („Schlüssel holen ↗“) in der App-Hülle: extLink in web/index.html ruft
window.pywebview.api.open_url (run_cuecore.ShellApi) und muss das Ergebnis auswerten.

Der Klick-Handler muss api.open_url(url) abwarten und {opened:false} auswerten — scheiterte der
Systembrowser nur im Bundle, tat der Link sonst still nichts. Hier läuft die tatsächliche Funktion aus
index.html in node mit einem kleinen DOM-Ersatz."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

INDEX = Path(__file__).resolve().parent.parent / "web" / "index.html"


def _fn(src: str, name: str) -> str:
    m = re.search(r"^function " + name + r"\(.*?^}\n", src, re.MULTILINE | re.DOTALL)
    assert m, f"function {name} nicht gefunden"
    return m.group(0)


HARNESS = r"""
class E { constructor(tag){ this.tagName=tag; this.children=[]; this.listeners={}; this.style={};
            this.className=''; this.textContent=''; this.parentElement=null; this.afterNodes=[]; }
  addEventListener(n,f){ this.listeners[n]=f; }
  appendChild(c){ c.parentElement=this; this.children.push(c); return c; }
  after(n){ this.afterNodes.push(n); } }
const document={createElement:t=>new E(t)};
const window={};
const t=(s)=>s;
%s
async function run(api){
  window.pywebview = api ? {api} : undefined;
  const head=new E('div'); const a=extLink('https://acoustid.org/new-application', 'Schlüssel holen'); head.appendChild(a);
  let prevented=false;
  a.listeners.click({preventDefault(){ prevented=true; }});
  await new Promise(r=>setTimeout(r,0));
  return {prevented, hints: head.afterNodes.map(n=>n.textContent + n.children.map(c=>c.textContent).join(''))};
}
(async()=>{
  const out={
    ok: await run({open_url:()=>Promise.resolve({opened:true})}),
    refused: await run({open_url:()=>Promise.resolve({opened:false, error:'x'})}),
    rejected: await run({open_url:()=>Promise.reject(new Error('bridge'))}),
    thrown: await run({open_url:()=>{ throw new Error('sync'); }}),
    browser: await run(null),
  };
  console.log(JSON.stringify(out));
})();
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node nicht installiert")
def test_extlink_zeigt_die_adresse_wenn_der_systembrowser_scheitert(tmp_path):
    src = INDEX.read_text(encoding="utf-8")
    el = re.search(r"^const el = .*$", src, re.MULTILINE).group(0)
    fns = "\n".join(_fn(src, n) for n in ("extLink", "_extLinkFailed") if re.search(r"^function " + n + r"\(", src, re.MULTILINE))
    f = tmp_path / "ext.js"
    f.write_text(HARNESS % (el + "\n" + fns), encoding="utf-8")
    r = subprocess.run(["node", str(f)], capture_output=True, text=True, timeout=10)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["ok"] == {"prevented": True, "hints": []}
    for case in ("refused", "rejected", "thrown"):
        assert out[case]["prevented"] is True, case
        assert len(out[case]["hints"]) == 1 and "https://acoustid.org/new-application" in out[case]["hints"][0], case
    assert out["browser"] == {"prevented": False, "hints": []}       # reiner Browser-Tab: normaler Link
