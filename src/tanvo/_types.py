from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

TERMINAL = {"SUCCEEDED", "FAILED", "CANCELED"}


@dataclass(frozen=True)
class Output:
    url: str
    mime: str
    # Songs only.
    cover: Optional[str] = None
    title: Optional[str] = None
    lyrics: Optional[str] = None
    seconds: Optional[float] = None

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Output":
        return cls(url=d["url"], mime=d.get("mime", ""), cover=d.get("cover"), title=d.get("title"), lyrics=d.get("lyrics"), seconds=d.get("seconds"))


@dataclass(frozen=True)
class Generation:
    id: str
    kind: str
    model: str
    prompt: str
    options: Dict[str, Any]
    cost: int
    status: str
    error: Optional[str]
    created_at: int
    outputs: List[Output] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Generation":
        return cls(
            id=d["id"],
            kind=d.get("kind", ""),
            model=d.get("model", ""),
            prompt=d.get("prompt", ""),
            options=d.get("options") or {},
            cost=d.get("cost", 0),
            status=d.get("status", ""),
            error=d.get("error"),
            created_at=d.get("createdAt", 0),
            outputs=[Output.from_dict(o) for o in d.get("outputs") or []],
        )

    @property
    def done(self) -> bool:
        return self.status in TERMINAL

    @property
    def succeeded(self) -> bool:
        return self.status == "SUCCEEDED"

    @property
    def url(self) -> Optional[str]:
        """The first output's URL, or None while running or after a failure."""
        return self.outputs[0].url if self.outputs else None


@dataclass(frozen=True)
class Model:
    id: str
    kind: str
    name: str
    free_tier: bool
    inputs: List[str]
    options: Dict[str, Any]
    defaults: Dict[str, Any]
    credits_at_defaults: int
    prompt_max_chars: int
    raw: Dict[str, Any] = field(repr=False, default_factory=dict)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Model":
        return cls(
            id=d["id"], kind=d.get("kind", ""), name=d.get("name", ""), free_tier=bool(d.get("free_tier")), inputs=d.get("inputs") or [],
            options=d.get("options") or {}, defaults=d.get("defaults") or {}, credits_at_defaults=d.get("credits_at_defaults", 0),
            prompt_max_chars=d.get("prompt_max_chars", 0), raw=d,
        )


@dataclass(frozen=True)
class Look:
    id: str
    name: str
    prompt: str
    model: str
    options: Dict[str, Any]
    example: Optional[str]
    url: str


@dataclass(frozen=True)
class AppInput:
    id: str
    label: str
    hint: str
    min: int
    max: int


@dataclass(frozen=True)
class App:
    slug: str
    kind: str
    name: str
    description: str
    category: Optional[str]
    model: str
    url: str
    inputs: List[AppInput]
    looks: List[Look]

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "App":
        return cls(
            slug=d["slug"], kind=d.get("kind", ""), name=d.get("name", ""), description=d.get("description", ""), category=d.get("category"),
            model=d.get("model", ""), url=d.get("url", ""),
            inputs=[AppInput(id=i["id"], label=i.get("label", ""), hint=i.get("hint", ""), min=i.get("min", 1), max=i.get("max", 1)) for i in d.get("inputs") or []],
            looks=[
                Look(id=l["id"], name=l.get("name", ""), prompt=l.get("prompt", ""), model=l.get("model", ""), options=l.get("options") or {},
                     example=(l.get("example") or {}).get("video") or (l.get("example") or {}).get("url"), url=l.get("url", ""))
                for l in d.get("looks") or []
            ],
        )

    def look(self, look_id: Optional[str] = None) -> Look:
        if look_id is None:
            return self.looks[0]
        for l in self.looks:
            if l.id == look_id:
                return l
        raise KeyError(f"{self.slug} has no look {look_id!r}; looks: {', '.join(l.id for l in self.looks)}")


@dataclass(frozen=True)
class Account:
    tier: str
    credits: int
    watermarked: bool
    concurrency: int
    email: Optional[str] = None
