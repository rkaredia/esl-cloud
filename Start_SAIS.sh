#!/bin/bash
cd "$(dirname "$0")"
docker-compose up -d
open "http://localhost:8000/admin"