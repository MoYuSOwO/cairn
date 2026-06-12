"""OpenAI 兼容 embedding 客户端。

统一 /v1/embeddings 接口, 支持 OpenAI / MiMo / Ollama / TEI 等所有兼容服务。
换模型只改配置, 不改代码。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class Embedder:
    """OpenAI 兼容 embedding 客户端。

    POST {base_url}/v1/embeddings
    body: {"model": model, "input": texts, "dimensions": dim (optional)}

    用法:
        embedder = Embedder(
            base_url="https://api.openai.com",
            api_key="sk-xxx",
            model="text-embedding-3-small",
        )
        vecs = await embedder.embed(["hello", "world"])
        assert len(vecs) == 2
        print(embedder.dim)  # 1536

    也支持本地模型:
        embedder = Embedder(
            base_url="http://localhost:11434",
            model="nomic-embed-text",
            dim=768,
        )
    """

    def __init__(
        self,
        base_url: str,
        api_key: str = "not-needed",
        model: str = "text-embedding-3-small",
        dim: int | None = None,
        timeout: float = 30.0,
        batch_size: int = 100,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self._dim: int | None = dim
        self.batch_size = batch_size
        self._client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )

    @property
    def dim(self) -> int:
        if self._dim is None:
            raise RuntimeError(
                "Embedding dimension not known yet; set dim explicitly "
                "or call embed() first to auto-detect"
            )
        return self._dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """批量生成 embedding, 超过 batch_size 自动分批发送。"""
        if not texts:
            return []

        all_results: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            batch_results = await self._embed_batch(batch)
            if self._dim is None and batch_results:
                self._dim = len(batch_results[0])
                logger.info("auto-detected embedding dim=%d", self._dim)
            all_results.extend(batch_results)

        return all_results

    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        """单次 API 调用, 不拆分。"""
        url = f"{self.base_url}/v1/embeddings"
        body: dict[str, Any] = {"model": self.model, "input": texts}
        if self._dim is not None:
            body["dimensions"] = self._dim

        resp = await self._client.post(url, json=body)
        resp.raise_for_status()
        data = resp.json()

        embeddings = sorted(data["data"], key=lambda d: d["index"])
        return [e["embedding"] for e in embeddings]

    async def embed_one(self, text: str) -> list[float]:
        """单条 embedding 快捷方法。"""
        results = await self.embed([text])
        return results[0]

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> Embedder:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()
