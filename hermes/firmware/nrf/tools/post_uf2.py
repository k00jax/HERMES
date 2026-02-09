Import("env")
import os
import subprocess

def make_uf2(source, target, env):
    build_dir = env.subst("$BUILD_DIR")
    hex_path = os.path.join(build_dir, "firmware.hex")
    uf2_path = os.path.join(build_dir, "firmware.uf2")

    uf2conv = os.path.join(env.subst("$PROJECT_DIR"), "tools", "uf2conv.py")

    if not os.path.exists(hex_path):
        print("No firmware.hex found at:", hex_path)
        return

    if not os.path.exists(uf2conv):
        print("Missing uf2conv.py at:", uf2conv)
        return

    family = "0xADA52840"  # nRF52840

    # Use PlatformIO's own python interpreter (works on Windows/macOS/Linux)
    python_exe = env.subst("$PYTHONEXE")  # provided by PlatformIO/SCons

    cmd = [
        python_exe,
        uf2conv,
        "--convert",
        "--family", family,
        "--output", uf2_path,
        hex_path,
    ]

    print("Creating UF2:")
    print(" ".join(cmd))
    subprocess.check_call(cmd)

env.AddPostAction("$BUILD_DIR/firmware.hex", make_uf2)
