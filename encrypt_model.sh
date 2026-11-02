#!/usr/bin/env bash
# تشفير موديل السلالة قبل رفعه على الريبو العام.
#   ./encrypt_model.sh path/to/cattle_model.h5            # بيعمل مفتاح جديد ويطبعه
#   MODEL_KEY=... ./encrypt_model.sh path/to/model.h5     # أو استخدم مفتاح عندك
# بعدها: حط المفتاح في GitHub secret باسم MODEL_KEY، وارفع models/cattle_model.h5.enc و models/cattle_model.h5.sha256
set -euo pipefail
src="${1:?usage: $0 path/to/cattle_model.h5}"
mkdir -p models
key="${MODEL_KEY:-$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')}"
MODEL_KEY="$key" openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -in "$src" -out models/cattle_model.h5.enc -pass env:MODEL_KEY
(cd "$(dirname "$src")" && sha256sum "$(basename "$src")" | sed 's/  .*/  cattle_model.h5/') > models/cattle_model.h5.sha256
echo "تم التشفير: models/cattle_model.h5.enc"
echo "المفتاح (احفظه في secret باسم MODEL_KEY): $key"
