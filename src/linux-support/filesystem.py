import os


def check_case_sensitivity(path):
    result = {"path": path, "exists": os.path.exists(path), "case_sensitive": None}
    if not result["exists"]:
        return result
    try:
        test_dir = os.path.join(path, ".ovls_case_test")
        os.makedirs(test_dir, exist_ok=True)
        upper = os.path.join(path, ".OVLS_CASE_TEST")
        result["case_sensitive"] = not os.path.exists(upper)
        os.rmdir(test_dir)
    except OSError:
        result["case_sensitive"] = None
    return result


def check_hardlink(path):
    result = {"path": path, "supported": False}
    if not os.path.isdir(path):
        return result
    try:
        src = os.path.join(path, ".ovls_hardlink_src")
        dst = os.path.join(path, ".ovls_hardlink_dst")
        with open(src, "w") as f:
            f.write("test")
        os.link(src, dst)
        result["supported"] = True
        os.remove(src)
        os.remove(dst)
    except OSError:
        for p in (src, dst):
            try:
                os.remove(p)
            except OSError:
                pass
    return result


def check_writable(path):
    return {"path": path, "writable": os.access(path, os.W_OK) if os.path.exists(path) else False}