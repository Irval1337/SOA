#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."

export PYTHONWARNINGS="ignore::FutureWarning"
rm -rf generated
fastapi-codegen \
  --input openapi/openapi.yaml \
  --output generated \
  --template-dir codegen/templates \
  --custom-visitor codegen/visitors/contract.py \
  --output-model-type pydantic_v2.BaseModel \
  --python-version 3.12 \
  --strict-nullable \
  --disable-timestamp
echo "generated/ is up to date with openapi/openapi.yaml"
