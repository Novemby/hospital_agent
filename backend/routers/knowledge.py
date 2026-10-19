from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


class KnowledgeChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=3, ge=1, le=8)


@router.post("/chat")
def knowledge_chat(payload: KnowledgeChatRequest):
    # Lazy imports allow the rest of the hospital app to start without RAG dependencies.
    try:
        from backend.services.rag.answers import RAGAnswerService
        service = RAGAnswerService.from_env()
    except (ImportError, ValueError, OSError, KeyError):
        raise HTTPException(status_code=503, detail="RAG 索引或配置不可用，请检查依赖、环境变量及索引文件。") from None
    try:
        return service.answer(payload.question, payload.top_k)
    except ValueError:
        raise HTTPException(status_code=422, detail="问题不能为空，top_k 必须为 1–8。") from None
