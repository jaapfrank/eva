"""Image-only release comparison shared by runtime and operator entrypoints."""
from copy import deepcopy


def image_update_services(installed: dict, candidate: dict) -> list[str]:
    """Reject configuration/data changes and return only changed image services."""
    expected = deepcopy(installed)
    if set(installed.get("services", {})) != set(candidate.get("services", {})):
        raise ValueError("update must retain the existing service inventory")
    changed = []
    for name, current in installed["services"].items():
        target = candidate["services"][name]
        image = target.get("image", "")
        if image != current.get("image"):
            if name not in {"core", "delegated-work-worker"}:
                raise ValueError("this updater changes only existing core and delegated worker images")
            if not image.startswith("sha256:") or len(image) != 71:
                raise ValueError("update requires an exact locally available image ID")
            int(image[7:], 16)
            expected["services"][name]["image"] = image
            changed.append(name)
    if expected != candidate:
        raise ValueError("image-only update cannot change mounts, configuration or data services")
    return changed

