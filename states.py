from aiogram.fsm.state import State, StatesGroup


class PollCreation(StatesGroup):
    waiting_question = State()
    waiting_options = State()
    waiting_log_choice = State()
    waiting_log_channel = State()


class VoteProcess(StatesGroup):
    solving_captcha = State()


class BroadcastStates(StatesGroup):
    waiting_message = State()
    waiting_type = State()


class ForceJoinStates(StatesGroup):
    waiting_channel = State()


class AdminLogStates(StatesGroup):
    waiting_channel = State()
