import json
import re
from typing import List, Optional, Dict, Tuple

import uvicorn
from fastapi import FastAPI, Request, Depends
from pydantic import BaseModel
from contextlib import asynccontextmanager


class LawLink(BaseModel):
    law_id: Optional[int] = None
    article: Optional[str] = None
    point_article: Optional[str] = None
    subpoint_article: Optional[str] = None


class LinksResponse(BaseModel):
    links: List[LawLink]


class TextRequest(BaseModel):
    text: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    with open("law_aliases.json", "r") as file:
        codex_aliases = json.load(file)
    
    # Build alias -> law_id lookup (case-insensitive)
    alias_to_id: Dict[str, int] = {}
    for law_id_str, aliases in codex_aliases.items():
        for alias in aliases:
            alias_to_id[alias.lower()] = int(law_id_str)

    # Precompile regexes used for detection
    # Abbreviation variants for units
    unit_patterns = {
        "subpoint": r"(?:подпункт(?:а|у|ом|е)?|пп\.|подп\.)",
        "point": r"(?:пункт(?:а|у|ом|е)?|п\.|ч\.|часть(?:и|ю)?)",
        "article": r"(?:статья|ст\.)",
    }

    # Numbers can be like 12, 4.6, 43.2-6, 1, 2, 3, 5 и 6, а, б, в, с
    num = r"[0-9]+(?:\.[0-9]+)*(?:-[0-9]+)?"
    letter = r"[А-Яа-яA-Za-z]"
    # Lists like "1, 2, 3" or "а, б и в" or "4.2 и 5.3"
    list_sep = r"\s*,\s*|\s+и\s+"
    list_num = rf"(?:{num}|{letter})(?:(?:{list_sep})(?:{num}|{letter}))*"

    # Law name capture: greedy up to end or punctuation; we will resolve via aliases
    # Common law abbreviations (e.g., НК РФ, УК РФ, ГК РФ, КоАП РФ, АПК РФ, ТК РФ, ГПК РФ, и т.д.)
    common_law_tail = r"(?:РФ|Российской Федерации|России)"

    # Example full pattern capturing optional subpoint, optional point/part, article, and law name
    pattern = rf"""
        (?P<subunit>(?:{unit_patterns['subpoint']})\s*(?P<subvalues>{list_num}))?\s*
        (?P<pointunit>(?:{unit_patterns['point']})\s*(?P<pointvalues>{list_num}))?\s*
        (?:(?:и\s+)?)
        (?P<articleunit>{unit_patterns['article']})\s*(?P<articlevalues>{list_num})\s*
        (?P<lawname>[\w\s«»\-\.\"№NоА-Яа-яA-Za-z0-9]+?(?:\s+(?:кодекс|Кодекс|закон|Закон|ФЗ|КоАП|АПК|ГПК|ГК|УК|НК)[^\n\r\.;,]*)?)
    """

    compiled_pattern = re.compile(pattern, re.VERBOSE | re.IGNORECASE | re.UNICODE)

    app.state.codex_aliases = codex_aliases
    app.state.alias_to_id = alias_to_id
    app.state.detect_regex = compiled_pattern
    print("🚀 Сервис запускается...")
    yield
    # Shutdown
    del codex_aliases
    print("🛑 Сервис завершается...")


def get_codex_aliases(request: Request) -> Dict:
    return request.app.state.codex_aliases


app = FastAPI(
    title="Law Links Service",
    description="Cервис для выделения юридических ссылок из текста",
    version="1.0.0",
    lifespan=lifespan
)


@app.post("/detect")
async def get_law_links(
    data: TextRequest,
    request: Request,
    codex_aliases: Dict = Depends(get_codex_aliases),
    ) -> LinksResponse:
    """
    Принимает текст и возвращает список юридических ссылок
    """
    text = data.text
    regex: re.Pattern = request.app.state.detect_regex
    alias_to_id: Dict[str, int] = request.app.state.alias_to_id

    def split_values(values: Optional[str]) -> List[str]:
        if not values:
            return []
        parts = re.split(r"\s*,\s*|\s+и\s+", values.strip())
        return [p.strip() for p in parts if p.strip()]

    def resolve_law_id(lawname: str) -> Optional[int]:
        # Try exact lowercase match first
        key = lawname.strip().lower()
        if key in alias_to_id:
            return alias_to_id[key]
        # Fallback: try to find any alias contained within the lawname or vice versa
        for alias, lid in alias_to_id.items():
            if alias in key or key in alias:
                return lid
        return None

    links: List[LawLink] = []
    for match in regex.finditer(text):
        lawname = (match.group("lawname") or "").strip().strip(".,;:")
        law_id = resolve_law_id(lawname)

        subvalues = split_values(match.group("subvalues"))
        pointvalues = split_values(match.group("pointvalues"))
        articlevalues = split_values(match.group("articlevalues"))

        # If multiple subpoints refer to the same article/point, expand to multiple LawLink
        # If there are multiple article numbers, we will create combinations per typical legal phrasing
        if not articlevalues:
            continue

        for article in articlevalues:
            # When there is a list of subpoints like "а, б и в", we generate separate LawLink entries
            if subvalues:
                for sub in subvalues:
                    links.append(LawLink(
                        law_id=law_id,
                        article=str(article),
                        point_article=pointvalues[0] if pointvalues else None,
                        subpoint_article=str(sub),
                    ))
            else:
                links.append(LawLink(
                    law_id=law_id,
                    article=str(article),
                    point_article=pointvalues[0] if pointvalues else None,
                    subpoint_article=None,
                ))

    return LinksResponse(links=links)


@app.get("/health")
async def health_check():
    """
    Проверка состояния сервиса
    """
    return {"status": "healthy"}



if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8978)