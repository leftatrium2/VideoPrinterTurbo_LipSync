from fastapi import APIRouter

from utils.result import result_succ


router = APIRouter()


@router.get("/")
async def root():
    return result_succ()
