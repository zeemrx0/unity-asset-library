#!/usr/bin/env python3
"""Read the Asset Store id that Unity embeds in downloaded .unitypackage files.

An Asset Store download is a gzip stream whose header carries an FEXTRA field. The
subfield ``A$`` holds UTF-8 JSON such as
``{"link": {"id": "177524", "type": "content"}, "id": "177524", "title": ...}``.
The id is authoritative; the embedded title can be stale ("Environment 2" for a
renamed ithappy pack), so callers must not use it for identity.

Reading bytes from a OneDrive cloud-only (dataless) file downloads the whole file,
so every read is guarded by ``is_materialized``. Anything unexpected returns None:
a header is a shortcut, never a requirement.
"""

import json
import os
import struct

SF_DATALESS = 0x40000000  # macOS: file content lives only in the cloud provider
MAX_HEADER = 65536
MAX_ID_LEN = 12
_GZIP_DEFLATE = b"\x1f\x8b\x08"
_FEXTRA = 0x04


def _label(value):
    if isinstance(value, dict):
        value = value.get("label")
    return value if isinstance(value, str) and value else None


def parse_header(head):
    """Store metadata from the first bytes of a .unitypackage, or None."""
    if not isinstance(head, (bytes, bytearray)) or len(head) < 12:
        return None
    if head[:3] != _GZIP_DEFLATE or not head[3] & _FEXTRA:
        return None
    (xlen,) = struct.unpack("<H", head[10:12])
    if 12 + xlen > len(head):
        return None
    extra = bytes(head[12:12 + xlen])
    obj = None
    pos = 0
    while pos + 4 <= len(extra):
        si1, si2 = extra[pos], extra[pos + 1]
        (length,) = struct.unpack("<H", extra[pos + 2:pos + 4])
        data = extra[pos + 4:pos + 4 + length]
        if len(data) != length:
            return None
        if si1 == 0x41 and si2 == 0x24:  # "A$"
            try:
                obj = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                return None
            break
        pos += 4 + length
    if not isinstance(obj, dict):
        return None

    link = obj.get("link")
    link_id = link.get("id") if isinstance(link, dict) else None
    ids = [value for value in (obj.get("id"), link_id) if value is not None]
    if not ids:
        return None
    ids = [str(value) for value in ids]
    if any(not value.isdigit() or len(value) > MAX_ID_LEN for value in ids):
        return None
    if len(set(ids)) != 1:
        return None
    version = obj.get("version")
    title = obj.get("title")
    return {
        "id": ids[0],
        "title": title if isinstance(title, str) else None,
        "publisher": _label(obj.get("publisher")),
        "category": _label(obj.get("category")),
        "version": version if isinstance(version, str) else None,
    }


def is_materialized(st):
    """True only when the file's leading bytes are already on local disk."""
    if getattr(st, "st_flags", 0) & SF_DATALESS:
        return False
    return st.st_blocks * 512 >= min(st.st_size, MAX_HEADER)


def read_store_header(path):
    """Parsed header for a local file. Never opens a cloud-only file."""
    try:
        st = os.stat(path)
        if not is_materialized(st):
            return None
        with open(path, "rb") as fh:
            head = fh.read(MAX_HEADER)
    except OSError:
        return None
    return parse_header(head)


def _load_cache(path):
    try:
        with open(path, encoding="utf-8") as fh:
            value = json.load(fh)
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def store_ids_for(assets, root, cache_path):
    """Return ({asset_key: header}, {asset_key: [conflicting ids]}).

    Results are cached by relative path, size and mtime so unchanged archives are
    never reopened. Cloud-only files are skipped and not cached, so they are checked
    again once OneDrive has downloaded them.
    """
    cache = _load_cache(cache_path)
    changed = False
    found, conflicts = {}, {}
    for asset in assets:
        if asset.get("non_store"):
            continue
        headers = {}
        for version in asset.get("versions") or []:
            rel = version.get("file")
            if not isinstance(rel, str) or not rel.endswith(".unitypackage"):
                continue
            full = os.path.join(root, rel)
            try:
                st = os.stat(full)
            except OSError:
                continue
            entry = cache.get(rel)
            if (isinstance(entry, dict) and entry.get("size") == st.st_size
                    and entry.get("mtime") == st.st_mtime):
                result = entry.get("result")
            else:
                if not is_materialized(st):
                    continue
                result = read_store_header(full)
                cache[rel] = {"size": st.st_size, "mtime": st.st_mtime, "result": result}
                changed = True
            if isinstance(result, dict) and result.get("id"):
                headers.setdefault(result["id"], result)
        if len(headers) == 1:
            found[asset["asset_key"]] = next(iter(headers.values()))
        elif len(headers) > 1:
            conflicts[asset["asset_key"]] = sorted(headers)
    if changed:
        from index_assets import write_atomic
        write_atomic(cache_path, json.dumps(cache, indent=1, sort_keys=True))
    return found, conflicts
