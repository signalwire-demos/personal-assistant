#!/bin/bash

curl -X POST https://ethan:jienic6Oor2tohsh@briankwest.ngrok.io/swml/ZBCL06uNc0LPZLgTKlnJLWD3guiV10D6yuGyqj6dywo/ \
  -H "Content-Type: application/json" \
  -d '{
  "call": {
    "call_id": "test-call-12345",
    "call_state": "created",
    "direction": "inbound",
    "from": "+19184249378",
    "from_number": "+19184249378",
    "to": "+16503820000",
    "to_number": "+16503820000",
    "type": "phone"
  },
  "vars": {}
}'
