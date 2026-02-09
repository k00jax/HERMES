Import("env")
import os
import subprocess

def make_uf2(source, target, env):
    build_dir = env.subst("$BUILD_DIR")
    progname = env.subst("$PROGNAME")  # defaults to "firmware" unless overridden

    hex_path = os.path.join(build_dir, f"{progname}.hex")
    uf2_path = os.path.join(build_dir, f"{progname}.uf2")

    uf2conv = os.path.join(env.subst("$PROJECT_DIR"), "tools", "uf2conv.py")
    families = os.path.join(env.subst("$PROJECT_DIR"), "tools", "uf2families.json")

    if not os.path.exists(hex_path):
        print("[post_uf2] No HEX found at:", hex_path)
        print("[post_uf2] Check and see: did PlatformIO produce a .hex for this env?")
        return

    if not os.path.exists(uf2conv):
        raise FileNotFoundError(f"[post_uf2] Missing uf2conv.py at {uf2conv}")

    # Microsoft uf2conv expects uf2families.json next to it
    if not os.path.exists(families):
        raise FileNotFoundError(f"[post_uf2] Missing uf2families.json at {families}")

    family = "0xADA52840"  # nRF52840
    python_exe = env.subst("$PYTHONEXE")

    cmd = [
        str(python_exe),
        str(uf2conv),
        "--convert",
        "--family", str(family),
        "--output", str(uf2_path),
        str(hex_path),
    ]

    print("[post_uf2] Creating UF2:")
    print(" ".join(cmd))

    subprocess.check_call(cmd)

# Hook: run after the hex is generated (uses $PROGNAME)
env.AddPostAction("$BUILD_DIR/${PROGNAME}.hex", make_uf2)
