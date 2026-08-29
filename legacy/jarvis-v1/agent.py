from tools import open_app, get_time, search_google, run_command

TOOLS = {
    "open_app": open_app,
    "get_time": lambda x: get_time(),
    "search_google": search_google,
    "run_command": run_command,
}


def execute_tool(tool, arg):
    if tool not in TOOLS:
        return "Unknown tool"

    try:
        return TOOLS[tool](arg)
    except Exception as e:
        return str(e)