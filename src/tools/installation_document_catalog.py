"""Canonical catalog identity shared by document restore transitions.

Gateway snapshots have always pinned original tool names in sorted order.
Non-gateway provider catalogs retain their original list-order identity.
"""


def canonical_document_catalog(catalog, preparation):
    if not isinstance(catalog, list):
        raise ValueError("document provider catalog must be a list")
    if "catalog_gateway_id" not in preparation:
        return catalog
    names = [tool.get("name") if isinstance(tool, dict) else None for tool in catalog]
    if any(not isinstance(name, str) or not name for name in names) or len(set(names)) != len(names):
        raise ValueError("document gateway catalog tool identity differs")
    return sorted(catalog, key=lambda tool: tool["name"])
