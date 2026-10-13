"""Exercise prepared redirect code and device configuration validation on the host."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
from types import SimpleNamespace
from urllib.parse import urlsplit


def verify(component: Path):
    transport = component / "esp_websocket_client"
    source = (transport / "esp_websocket_client.c").read_text()
    begin = source.index("            else if (WS_HTTP_REDIRECT(result)) {")
    end = source.index("\n#endif", begin)
    branch = source[begin:end].replace("else if", "if", 1)
    # Execute the actual prepared redirect branch, replacing only transport,
    # allocation ownership and logging around it with controlled host inputs.
    harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#define WS_HTTP_REDIRECT(code) ((code >= 300) && (code < 400))
#define WEBSOCKET_ERROR_TYPE_TCP_TRANSPORT 1
#define WEBSOCKET_STATE_INIT 0
#define ESP_LOGI(...) ((void)0)
typedef struct { bool disable_redirect; char *uri; int port; } Config;
typedef struct { Config *config; int state; void *transport; } Client;
static int redirects, aborts;
static const char *esp_transport_ws_get_redir_uri(void *p) { return "wss://different.invalid/collect"; }
static int esp_transport_get_default_port(void *p) { return 443; }
static void esp_websocket_client_set_uri(Client *c,const char *s) { ++redirects; }
static void esp_websocket_client_error(Client *c,const char *s) {}
static void esp_websocket_client_abort_connection(Client *c,int error) { ++aborts; }
static void scenario(bool blocked,int result) {
  Config cfg={blocked,strdup("wss://configured.invalid/eva-speaker/v1"),443};
  Client instance={&cfg,0,NULL}; Client *client=&instance;
  redirects=aborts=0;
  do {
__REDIRECT_BRANCH__
  } while (0);
  if (WS_HTTP_REDIRECT(result) && blocked) {
    assert(aborts==1 && redirects==0);
    assert(!strcmp(cfg.uri,"wss://configured.invalid/eva-speaker/v1"));
  } else if (WS_HTTP_REDIRECT(result)) { assert(redirects==1 && aborts==0); }
  else { assert(!redirects && !aborts); }
  free(cfg.uri);
}
int main(void) {
  int redirects[]={301,302,303,307,308};
  for (unsigned i=0;i<sizeof(redirects)/sizeof(redirects[0]);++i) scenario(true,redirects[i]);
  scenario(false,302); scenario(true,101);
}
'''.replace("__REDIRECT_BRANCH__", branch)
    with tempfile.TemporaryDirectory(prefix="eva-auth-probe-") as temporary:
        p = Path(temporary)
        (p / "redirect.c").write_text(harness)
        subprocess.run(["gcc", "-fsanitize=undefined", str(p / "redirect.c"), "-o", str(p / "redirect")],
                       check=True, capture_output=True, text=True)
        subprocess.run([str(p / "redirect")], check=True, capture_output=True, text=True)
    tree = ast.parse((component / "__init__.py").read_text())
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                and node.name == "validate_eva_transport"]
    assert len(selected) == 1
    context = {"cv": SimpleNamespace(Invalid=ValueError), "re": re, "urlsplit": urlsplit,
               "CONF_URL": "url"}  # imported ESPHome constant
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name.startswith("CONF_") and isinstance(node.value, ast.Constant):
                context[name] = node.value.value
    exec(compile(ast.Module(body=selected, type_ignores=[]), "prepared-validation", "exec"), context)
    validate = context["validate_eva_transport"]
    valid = {"eva_pcm_protocol": True, "url": "wss://configured.invalid/eva-speaker/v1",
             "auto_provision": False, "eva_credential": "A"*43,
             "eva_server_ca": "-----BEGIN CERTIFICATE-----\ncompile-only\n-----END CERTIFICATE-----"}
    validate(valid)
    rejected = []
    for label, changed in [
        ("plaintext", {"url": "ws://configured.invalid/eva-speaker/v1"}),
        ("dynamic-provisioning", {"auto_provision": True}),
        ("userinfo", {"url": "wss://user@configured.invalid/eva-speaker/v1"}),
        ("wrong-path", {"url": "wss://configured.invalid/another"}),
        ("query-credential", {"url": valid["url"]+"?token=secret"}),
        ("missing-ca", {"eva_server_ca": ""}),
        ("header-injection", {"eva_credential": "A"*43+"\r\nInjected: value"}),
        ("legacy-with-credential", {"eva_pcm_protocol": False}),
    ]:
        try:
            validate(valid | changed)
        except ValueError:
            rejected.append(label)
        else:
            raise AssertionError(f"unsafe configuration admitted: {label}")
    return {"status": "passed", "redirectCodesRejectedBeforeUriChange": [301,302,303,307,308],
            "legacyRedirectBehaviorRetained": True, "unsafeConfigurationsRejected": rejected,
            "transportSourceSha256": hashlib.sha256(source.encode()).hexdigest(),
            "scope": "Actual prepared C redirect branch under host transport stubs/UBSan; actual Python validation function. Not a physical TLS handshake."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--component", required=True, type=Path)
    print(json.dumps(verify(parser.parse_args().component), indent=2))
