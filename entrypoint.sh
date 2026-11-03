#!/usr/bin/env bash
# بيفك تشفير موديل السلالة (لو MODEL_KEY موجود) وبعدين بيشغّل البوت. فشل فك التشفير مش بيوقف البوت.
set -u
MODELS_DIR="${MODELS_DIR:-models}"
if [ -n "${MODEL_KEY:-}" ] && [ -f "$MODELS_DIR/cattle_model.h5.enc" ] && [ ! -f "$MODELS_DIR/cattle_model.h5" ]; then
  if openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in "$MODELS_DIR/cattle_model.h5.enc" -out "$MODELS_DIR/cattle_model.h5" -pass env:MODEL_KEY \
     && (cd "$MODELS_DIR" && sha256sum -c cattle_model.h5.sha256 >/dev/null); then
    echo "cattle model decrypted"
  else
    echo "WARNING: could not decrypt the cattle model — breed recognition will be disabled" >&2
    rm -f "$MODELS_DIR/cattle_model.h5"
  fi
fi
exec python bot.py
