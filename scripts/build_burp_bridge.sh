#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
JAR="$ROOT/integrations/burp/build/montoya-api-2026.7.jar"
OUTPUT="$ROOT/integrations/burp/build/BugHunterBurpBridge.jar"
mkdir -p "$(dirname "$JAR")" "$ROOT/integrations/burp/build/classes"
command -v javac >/dev/null || { echo 'Install Java 21 (e.g. brew install openjdk@21).'; exit 1; }
if [[ ! -s "$JAR" ]]; then
  curl --fail --location --proto '=https' --tlsv1.2 --output "$JAR" 'https://repo.maven.apache.org/maven2/net/portswigger/burp/extensions/montoya-api/2026.7/montoya-api-2026.7.jar'
fi
javac -cp "$JAR" -d "$ROOT/integrations/burp/build/classes" "$ROOT/integrations/burp/src/main/java/com/bughunter/BugHunterBurpBridge.java"
jar cf "$OUTPUT" -C "$ROOT/integrations/burp/build/classes" .
printf '\nCreated: %s\nAdd it in Burp Suite > Extensions > Installed > Add > Java extension.\nEnsure your test targets are inside Burp scope.\n' "$OUTPUT"
