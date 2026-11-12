#!/system/bin/sh
# GameLab Hide — customize.sh
# Appends DenyList packages for common mobile ACE targets.
# Runs inside Magisk's installer sandbox (root).

ui_print "- GameLab Hide installing"

# Reinforce Zygisk + DenyList enforcement
magiskhide=
if command -v magiskhide >/dev/null 2>&1; then
  magiskhide=$(magiskhide status 2>/dev/null)
fi

ui_print "  Zygisk DenyList packages:"
# Common targets
for pkg in \
    com.pubg.imobile \
    com.tencent.ig \
    com.activision.callofduty.shooter \
    com.garena.game.codm \
    com.dts.freefireth \
    com.dts.freefiremax \
    com.epicgames.fortnite ; do
  ui_print "    + $pkg"
  magisk --denylist add $pkg 2>/dev/null || true
done

# turn on enforcement
magisk --denylist enforce 2>/dev/null || true

ui_print "- Done. Reboot after Shamiko + safetynet-fix installed."
