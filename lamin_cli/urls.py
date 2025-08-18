def decompose_url(url: str) -> tuple[str, str, str]:
    assert any(keyword in url for keyword in ["transform", "artifact", "collection"])
    for entity in ["transform", "artifact", "collection"]:
        if entity in url:
            break
    uid = url.split(f"{entity}/")[1]
    instance_slug = "/".join(url.split("/")[3:5])
    return instance_slug, entity, uid
