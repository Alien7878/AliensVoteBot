from aiogram import Router

from handlers.start import router as start_router
from handlers.poll import router as poll_router
from handlers.vote import router as vote_router
from handlers.admin import router as admin_router
from handlers.broadcast import router as broadcast_router


def register_all_routers(dp):
    dp.include_router(admin_router)
    dp.include_router(broadcast_router)
    dp.include_router(start_router)
    dp.include_router(poll_router)
    dp.include_router(vote_router)
