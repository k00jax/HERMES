Import("env")
import os

# Force PlatformIO/SCons to use the system ARM GCC toolchain on linux_aarch64
# because PlatformIO's prebuilt toolchain-gccarmnoneeabi package isn't available.
tool_prefix = "arm-none-eabi-"

env.Replace(
    AR=tool_prefix + "ar",
    AS=tool_prefix + "as",
    CC=tool_prefix + "gcc",
    CXX=tool_prefix + "g++",
    GDB=tool_prefix + "gdb",
    OBJCOPY=tool_prefix + "objcopy",
    OBJDUMP=tool_prefix + "objdump",
    RANLIB=tool_prefix + "ranlib",
    SIZETOOL=tool_prefix + "size",
)

# Ensure PATH includes /usr/bin where the toolchain lives
env.PrependENVPath("PATH", "/usr/bin")

print("[odroid_toolchain] Using system arm-none-eabi toolchain from /usr/bin")
