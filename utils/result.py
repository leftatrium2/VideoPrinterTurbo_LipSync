from utils import const


def result_succ(data=None) -> dict:
    if data is None:
        data = {}
    return {
        "code": const.GLOBAL_SUCC,
        "msg": "success",
        "data": data,
    }
