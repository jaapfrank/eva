"""Non-secret ordinary resource catalog projection and workspace rendering."""
from urllib.parse import urlsplit
V1 = "P2NonSecretResourceCatalog/v1"
V2 = "P2NonSecretResourceCatalog/v2"

def catalog_resources(catalog):
    if not isinstance(catalog, dict):
        raise ValueError("resource catalog object required")
    if catalog.get("contract") == V1 and set(catalog) == {"contract", "resource_origins"}:
        resources = [{"origin": origin} for origin in catalog["resource_origins"]] if isinstance(catalog["resource_origins"], list) else None
    elif catalog.get("contract") == V2 and set(catalog) == {"contract", "resources"}:
        resources = catalog["resources"]
    else:
        raise ValueError("resource catalog shape differs")
    if not isinstance(resources, list) or not resources:
        raise ValueError("resource list required")
    seen = set()
    for resource in resources:
        required = {"origin"} if catalog["contract"] == V1 else {"origin", "discovery"}
        if not isinstance(resource, dict) or set(resource) != required:
            raise ValueError("resource fields differ")
        origin = resource["origin"]
        if not isinstance(origin, str) or any(c.isspace() for c in origin):
            raise ValueError("complete non-secret origin required")
        parsed = urlsplit(origin)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path or origin != parsed.scheme + "://" + parsed.netloc:
            raise ValueError("complete non-secret origin required")
        try:
            parsed.port
        except ValueError as error:
            raise ValueError("invalid origin port") from error
        if origin in seen:
            raise ValueError("duplicate origin")
        seen.add(origin)
        if catalog["contract"] == V2 and (not isinstance(resource["discovery"], str) or not resource["discovery"].strip()):
            raise ValueError("original discovery descriptor required")
    return [dict(resource) for resource in resources]

def project_resource_catalog(services):
    # Only public access metadata; records, credentials and task content are excluded.
    catalog = {"contract": V2, "resources": [
        {"origin": service["origin"], "discovery": service["contract"]["discovery"]}
        for service in services
    ]}
    catalog_resources(catalog)
    return catalog

def render_service_access(catalog, expected_origins):
    resources = catalog_resources(catalog)
    if [resource["origin"] for resource in resources] != list(expected_origins):
        raise ValueError("catalog does not match admitted service origins")
    lines = ["# Available service access", ""]
    for resource in resources:
        lines.append("- Service origin: " + resource["origin"])
        if "discovery" in resource:
            lines.append("  Discovery: " + resource["discovery"])
    return "\n".join(lines) + "\n\nYour own service capability is stored in " + chr(96) + ".eva-fixture-capability" + chr(96) + " in this workspace. The services accept it as a bearer capability.\n"
