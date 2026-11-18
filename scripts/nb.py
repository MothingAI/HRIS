#!/usr/bin/env python3
"""NocoBase HTTP API helper for the HRIS demo.

Small, dependency-light client used by the seed / verify scripts. Uses only the
standard library + requests. Read the admin password from the environment, not
from command-line flags or committed files.

Env vars:
  NB_BASE    base URL (default http://127.0.0.1:13000)
  NB_EMAIL   login email
  NB_PASSWORD login password
"""
import json
import os
import sys

import requests


class NbClient:
    def __init__(self, base=None, email=None, password=None):
        self.base = (base or os.environ.get("NB_BASE", "http://127.0.0.1:13000")).rstrip("/")
        self.email = email or os.environ.get("NB_EMAIL")
        self.password = password or os.environ.get("NB_PASSWORD")
        self.token = None
        self.session = requests.Session()

    def signin(self, email=None, password=None):
        email = email or self.email
        password = password or self.password
        if not email or not password:
            raise RuntimeError("NB_EMAIL / NB_PASSWORD (or args) required for signin")
        r = self.session.post(f"{self.base}/api/auth:signIn", json={"email": email, "password": password}, timeout=30)
        r.raise_for_status()
        data = r.json()["data"]
        self.token = data["token"]
        self.user = data["user"]
        return self.user

    def _headers(self):
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def req(self, method, path, json_body=None, params=None):
        url = f"{self.base}{path}"
        r = self.session.request(method, url, json=json_body, params=params, headers=self._headers(), timeout=60)
        return r

    def call(self, method, path, json_body=None, params=None, raw=False):
        r = self.req(method, path, json_body, params)
        if raw:
            return r
        try:
            return r.json()
        except ValueError:
            return {"_status": r.status_code, "_text": r.text}

    # ---- conveniences -----------------------------------------------------
    def list(self, collection, params=None):
        return self.call("GET", f"/api/{collection}:list", params=params)

    def get(self, collection, pk, params=None):
        return self.call("GET", f"/api/{collection}:get", params={"filterByTk": pk, **(params or {})})

    def create(self, collection, body):
        return self.call("POST", f"/api/{collection}:create", json_body=body)

    def update(self, collection, pk, body):
        return self.call("POST", f"/api/{collection}:update", json_body=body, params={"filterByTk": pk})

    def destroy(self, collection, pk):
        return self.call("POST", f"/api/{collection}:destroy", json_body={}, params={"filterByTk": pk})

    def create_user(self, email, password, nickname, username=None):
        return self.create("users", {
            "email": email,
            "password": password,
            "nickname": nickname,
            "username": username or email,
        })

    def signin_token(self, email, password):
        return self.signin(email=email, password=password) or self.token


if __name__ == "__main__":
    sys.exit(0)
