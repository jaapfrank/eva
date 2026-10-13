"""Explicit optional connector scope, bound to the existing installer journal."""
import ipaddress
from eva.operations.installer_voice_selection import _read, _save


def connector_scope(public_https, local_networks):
    if type(public_https) is not bool or not isinstance(local_networks, list) or len(local_networks)>32:
        raise ValueError('connector scope invalid')
    networks=[]
    for value in local_networks:
        if not isinstance(value,str):raise ValueError('canonical network text required')
        network=ipaddress.ip_network(value,strict=True)
        if (not network.is_private or network.is_loopback or network.is_link_local
                or network.is_multicast or network.is_unspecified):
            raise ValueError('explicit private network scope required')
        networks.append(str(network))
    if networks!=sorted(set(networks)) or (not public_https and not networks):
        raise ValueError('explicit nonempty connector scope required')
    return {'public_https':public_https,'local_networks':networks}


def confirm_connectors(verifier, *, public_https, local_networks):
    scope=connector_scope(public_https,local_networks)
    workflow=verifier.capability_workflow
    current=workflow.status().step('adaptive_connector')
    if current.state=='ready':
        workflow.record_invalidation(step_id='adaptive_connector',evidence_sha256=current.evidence_sha256)
    status=workflow.status()
    if status.step('adaptive_connector').state not in {'selected','blocked'}:
        raise ValueError('select optional connectors first')
    digest=_save(verifier,{'contract':'EvaInstallerConnectorScope/v1','scope':scope,
                         'installed':False,'provider_access_verified':False})
    workflow.record_verification(step_id='adaptive_connector',outcome='ready',evidence_sha256=digest,
        expected_revision=status.revision,expected_head_sha256=status.head_sha256)
    return digest


def load_connector_scope(plan,verifier):
    step=next(row for row in plan['capabilities'] if row['step_id']=='adaptive_connector')
    if step['state']!='ready':raise ValueError('confirmed connector scope required')
    payload=_read(verifier,step['evidence_sha256'])
    if payload.get('contract')!='EvaInstallerConnectorScope/v1':raise ValueError('connector scope receipt invalid')
    return connector_scope(**payload['scope'])
