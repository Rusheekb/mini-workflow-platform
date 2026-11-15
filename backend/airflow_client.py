"""A small client for Airflow 3's REST API (only what the platform needs).

Auth: POST /auth/token with username/password returns a JWT; every call sends it as a Bearer token.
The token is cached and refreshed once if Airflow rejects it (expired).
"""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger("mwp.airflow")


class AirflowUnavailable(Exception):
    """Airflow couldn't be reached or returned an unexpected error."""


class AirflowClient:
    def __init__(self, base_url: str, username: str, password: str, timeout: float = 5.0):
        # Always set a timeout: without one, a hung Airflow would hang every API request too.
        self._http = httpx.Client(base_url=base_url, timeout=timeout)
        self._username = username
        self._password = password
        self._token: str | None = None

    def _login(self) -> str:
        response = self._http.post(
            "/auth/token", json={"username": self._username, "password": self._password}
        )
        response.raise_for_status()
        return response.json()["access_token"]

    def _get(self, path: str, params: dict | None = None) -> httpx.Response:
        try:
            if self._token is None:
                self._token = self._login()
            response = self._http.get(path, params=params, headers={"Authorization": f"Bearer {self._token}"})
            if response.status_code in (401, 403):  # token expired: log in again, retry once
                self._token = self._login()
                response = self._http.get(
                    path, params=params, headers={"Authorization": f"Bearer {self._token}"}
                )
            return response
        except httpx.HTTPError as exc:
            log.warning("Airflow request %s failed: %s", path, exc)
            raise AirflowUnavailable(str(exc)) from exc

    def recent_runs(self, dag_id: str, limit: int = 5) -> list[dict]:
        """Newest runs first. [] if Airflow hasn't parsed this DAG yet (404)."""
        response = self._get(
            f"/api/v2/dags/{dag_id}/dagRuns", params={"order_by": "-run_after", "limit": limit}
        )
        if response.status_code == 404:
            return []
        if response.is_error:
            raise AirflowUnavailable(f"GET dagRuns for {dag_id} returned {response.status_code}")
        return response.json()["dag_runs"]
