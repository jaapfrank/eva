from __future__ import annotations

import importlib.util
from pathlib import Path
import shlex
from subprocess import CompletedProcess

import pytest


PATH = Path(__file__).resolve().parents[3] / "deploy/owner-browser/recovery/recover_firewall.py"
SPEC = importlib.util.spec_from_file_location("browser_firewall_recovery", PATH)
recovery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recovery)

CONFIG = {
    "instance": "eva2-browser",
    "networks": [{"address": "192.168.0.2"}, {"address": "192.168.16.2"}],
}


def own_hooks(hook):
    return [
        ["-A", hook, "-s", network["address"] + "/32",
         "-m", "comment", "--comment", CONFIG["instance"], "-j", recovery.CHAINS[hook]]
        for network in CONFIG["networks"]
    ]


def other_hook(hook, address="10.254.4.2/32", *, comment=True):
    return ["-A", hook, "-s", address, *(
        ["-m", "comment", "--comment", "other-installation"] if comment else []
    ), "-j", "OTHER_BROWSER"]


class Firewall:
    def __init__(self, hooks):
        self.rules = {
            chain: [["-A", chain, *rule] for rule in recovery.desired_rules(hook)]
            for hook, chain in recovery.CHAINS.items()
        }
        self.rules.update(hooks)
        self.mutations = []

    def __call__(self, operation, chain, *args, check=True):
        if operation == "-S":
            result = CompletedProcess(
                [], 0 if chain in self.rules else 1,
                "\n".join(shlex.join(rule) for rule in self.rules.get(chain, [])), "",
            )
        elif operation == "-C":
            result = CompletedProcess(
                [], 0 if ["-A", chain, *args] in self.rules[chain] else 1, "", ""
            )
        elif operation == "-A":
            self.mutations.append((operation, chain, *args))
            self.rules[chain].append(["-A", chain, *args])
            result = CompletedProcess([], 0, "", "")
        elif operation == "-I":
            assert args[0] == "1"
            self.mutations.append((operation, chain, *args))
            self.rules[chain].insert(0, ["-A", chain, *args[1:]])
            result = CompletedProcess([], 0, "", "")
        elif operation == "-N":
            assert chain not in self.rules
            self.mutations.append((operation, chain))
            self.rules[chain] = []
            result = CompletedProcess([], 0, "", "")
        else:
            pytest.fail(f"Unexpected firewall operation: {operation}")
        if check and result.returncode:
            raise RuntimeError("fixture command failed")
        return result


@pytest.fixture(autouse=True)
def no_host_commands(monkeypatch):
    def reject(*args, **kwargs):
        pytest.fail("Recovery tests must not run host commands")
    monkeypatch.setattr(recovery, "run", reject)


@pytest.mark.parametrize("layout", ["own_first", "other_first", "interleaved", "bare_source_jump"])
def test_disjoint_installation_hooks_preserve_order_without_mutation(monkeypatch, layout):
    hooks = {}
    for hook in recovery.CHAINS:
        own = own_hooks(hook)
        other = [other_hook(hook), other_hook(hook, "10.254.3.2/32")]
        if layout == "own_first":
            rules = [*reversed(own), *other]
        elif layout == "other_first":
            rules = [*other, *reversed(own)]
        elif layout == "interleaved":
            rules = [other[0], own[0], other[1], own[1]]
        else:
            rules = [other_hook(hook, comment=False), *own]
        hooks[hook] = [*rules, ["-A", hook, "-j", "RETURN"]]
    firewall = Firewall(hooks)
    before = {chain: [rule[:] for rule in rules] for chain, rules in firewall.rules.items()}
    monkeypatch.setattr(recovery, "iptables", firewall)

    recovery.restore_rules(CONFIG)
    recovery.restore_rules(CONFIG)

    assert firewall.rules == before
    assert firewall.mutations == []


@pytest.mark.parametrize("unsafe", [
    ["-j", "ACCEPT"],
    ["-j", "RETURN"],
    ["-d", "10.254.4.2/32", "-j", "ACCEPT"],
    ["-s", "0.0.0.0/0", "-j", "ACCEPT"],
    ["-s", "192.168.0.0/16", "-j", "OTHER_BROWSER"],
    ["-s", "192.168.0.2/32", "-j", "OTHER_BROWSER"],
    ["-s", "192.168.16.2/32", "-j", "OTHER_BROWSER"],
    ["!", "-s", "10.254.4.2/32", "-j", "ACCEPT"],
    ["-s", "!", "10.254.4.2/32", "-j", "ACCEPT"],
    ["-s", "10.0.0.0/8", "-j", "OTHER_BROWSER"],
    ["-s", "10.254.4.2/32", "-m", "conntrack", "--ctstate", "NEW", "-j", "ACCEPT"],
    ["-s", "10.254.4.2/32", "-g", "OTHER_BROWSER"],
    ["-s", "2001:db8::1/128", "-j", "OTHER_BROWSER"],
    ["-s", "host.example", "-j", "OTHER_BROWSER"],
])
@pytest.mark.parametrize("hook", ["DOCKER-USER", "INPUT"])
def test_broad_overlapping_or_unrecognized_prefix_fails_closed(monkeypatch, hook, unsafe):
    hooks = {name: own_hooks(name) for name in recovery.CHAINS}
    # Also exercise an unsafe rule between our two hooks, after partial coverage.
    hooks[hook].insert(1, ["-A", hook, *unsafe])
    firewall = Firewall(hooks)
    monkeypatch.setattr(recovery, "iptables", firewall)

    with pytest.raises(RuntimeError, match="browser firewall readback differs"):
        recovery.restore_rules(CONFIG)

    assert firewall.mutations == []


@pytest.mark.parametrize("hook", ["DOCKER-USER", "INPUT"])
def test_duplicate_hook_cannot_substitute_for_second_source(monkeypatch, hook):
    hooks = {name: own_hooks(name) for name in recovery.CHAINS}
    hooks[hook].insert(1, hooks[hook][0][:])
    firewall = Firewall(hooks)
    monkeypatch.setattr(recovery, "iptables", firewall)

    with pytest.raises(RuntimeError, match="browser firewall readback differs"):
        recovery.restore_rules(CONFIG)

    assert firewall.mutations == []


@pytest.mark.parametrize("hook", ["DOCKER-USER", "INPUT"])
def test_existing_chain_difference_is_not_repaired_or_accepted(monkeypatch, hook):
    firewall = Firewall({name: own_hooks(name) for name in recovery.CHAINS})
    firewall.rules[recovery.CHAINS[hook]][0] = ["-A", recovery.CHAINS[hook], "-j", "ACCEPT"]
    monkeypatch.setattr(recovery, "iptables", firewall)

    with pytest.raises(RuntimeError, match="existing browser firewall chain differs"):
        recovery.restore_rules(CONFIG)

    assert firewall.mutations == []


def test_missing_own_hook_and_interrupted_chain_still_restore_only_own_rules(monkeypatch):
    hooks = {hook: [other_hook(hook), *own_hooks(hook)[1:]] for hook in recovery.CHAINS}
    firewall = Firewall(hooks)
    for chain in recovery.CHAINS.values():
        firewall.rules[chain].pop()
    monkeypatch.setattr(recovery, "iptables", firewall)

    recovery.restore_rules(CONFIG)

    assert len(firewall.mutations) == 4
    assert all(call[0] in {"-A", "-I"} for call in firewall.mutations)
    for hook, chain in recovery.CHAINS.items():
        assert firewall.rules[chain] == [
            ["-A", chain, *rule] for rule in recovery.desired_rules(hook)
        ]
        assert firewall.rules[hook] == [own_hooks(hook)[0], other_hook(hook), own_hooks(hook)[1]]
        assert other_hook(hook) in firewall.rules[hook]
