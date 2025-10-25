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
    # Abbreviation variants for units - expanded to handle more cases
    unit_patterns = {
        "subpoint": r"(?:подпункт(?:а|у|ом|е)?|пп\.|подп\.|подпункты)",
        "point": r"(?:пункт(?:а|у|ом|е)?|п\.|ч\.|часть(?:и|ю)?|части)",
        "article": r"(?:статья|ст\.|статьи)",
    }

    # Enhanced number patterns to handle complex cases like 1048.15-8, 6.9-13, 1930.7
    num = r"[0-9]+(?:\.[0-9]+)*(?:-[0-9]+)?"
    letter = r"[А-Яа-яA-Za-z]"
    # Lists like "1, 2, 3" or "а, б и в" or "4.2 и 5.3" or "я, 26, 29, 22, 3"
    list_sep = r"\s*,\s*|\s+и\s+"
    list_num = rf"(?:{num}|{letter})(?:(?:{list_sep})(?:{num}|{letter}))*"

    # Simplified and more robust law name capture
    law_name_pattern = r"""
        (?P<lawname>
            [А-Яа-я\w\s«»\-\.\"№Nо]+?
            (?:кодекс|Кодекс|закон|Закон|ФЗ|КоАП|АПК|ГПК|ГК|УК|НК|Указ|указ|Положение|положение)
            [А-Яа-я\w\s«»\-\.\"№Nо]*
        )
    """

    # Enhanced full pattern with better law name matching
    pattern = rf"""
        (?P<subunit>(?:{unit_patterns['subpoint']})\s*(?P<subvalues>{list_num}))?\s*
        (?P<pointunit>(?:{unit_patterns['point']})\s*(?P<pointvalues>{list_num}))?\s*
        (?:(?:и\s+)?)
        (?P<articleunit>{unit_patterns['article']})\s*(?P<articlevalues>{list_num})\s*
        {law_name_pattern}
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
        # Clean and normalize the law name
        key = lawname.strip().lower()
        
        # Try exact match first
        if key in alias_to_id:
            return alias_to_id[key]
        
        # Try to find the best matching alias
        best_match = None
        best_score = 0
        
        for alias, lid in alias_to_id.items():
            # Calculate similarity score
            if alias in key:
                score = len(alias) / len(key)
                if score > best_score:
                    best_score = score
                    best_match = lid
            elif key in alias:
                score = len(key) / len(alias)
                if score > best_score:
                    best_score = score
                    best_match = lid
        
        # If we found a reasonable match (at least 50% similarity), return it
        if best_score > 0.5:
            return best_match
            
        # Special cases for common patterns - more comprehensive matching
        if "воздушного кодекса" in key or "воздушный кодекс" in key:
            return 3  # Воздушный кодекс
        elif "бюджетного кодекса" in key or "бюджетный кодекс" in key:
            return 1  # Бюджетный кодекс
        elif "налогового кодекса" in key or "налоговый кодекс" in key:
            return 15  # Налоговый кодекс
        elif "лесного кодекса" in key or "лесной кодекс" in key:
            return 14  # Лесной кодекс
        elif "земельного кодекса" in key or "земельный кодекс" in key:
            return 16  # Земельный кодекс
        elif "семейного кодекса" in key or "семейный кодекс" in key:
            return 8  # Семейный кодекс
        elif "гражданского процессуального кодекса" in key or "гпк" in key:
            return 6  # ГПК
        elif "кодекса об административных правонарушениях" in key or "коап" in key:
            return 17  # КоАП
        elif "кодекса внутреннего водного транспорта" in key:
            return 19  # Кодекс внутреннего водного транспорта
        elif "указа президента" in key or "указ президента" in key:
            # For presidential decrees, we'll need to match specific ones
            # This is a fallback - specific cases should be in law_aliases.json
            return None
            
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