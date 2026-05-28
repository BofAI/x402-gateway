---
name: acme-weather
title: "Acme Weather API"
description: "Current and forecast weather data"
logo: https://example.com/logo.png
use_case: "Use for city-level weather lookup"
category: data
service_url: https://gw.example.com/acme-weather
openapi:
  url: https://api.example.com/openapi.json
tags: [weather, data]
---

## Spend-aware usage

- Prefer current weather before forecast when possible.

## When to use

- Use for paid weather lookup.

## When NOT to use

- Do not use for historical climate analysis.
