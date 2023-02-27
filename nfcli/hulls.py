"""Handles static content in `data/hulls` folder."""

import json
from glob import glob
from typing import Optional


class Hulls:
    def __init__(self) -> None:
        self.hulls: dict[str, dict] = {}
        self.tags = self._load_json("data/tags.json")
        self._load_hulls()

    def _load_hulls(self):
        for file in glob("data/hulls/*.json"):
            hull = self._load_json(file)
            key = hull["key"]
            self.hulls[f"{key}"] = hull

    def _load_json(self, path: str) -> dict:
        try:
            f = open(path, "r")
        except EnvironmentError:
            return {}
        else:
            return json.load(f)

    def get_component_tag(self, socket: str) -> Optional[str]:
        if socket in self.tags:
            return self.tags.get(socket)
        return None

    def get_data(self, hull: str) -> dict:
        if hull in self.hulls:
            return self.hulls.get(hull)
        return {}


hulls = Hulls()