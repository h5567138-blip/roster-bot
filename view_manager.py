import traceback

async def register_views(bot, views):
    print("==============================")
    print("REGISTER PERSISTENT VIEWS")
    print("==============================")

    for name, factory in views:
        try:
            view = factory()
            bot.add_view(view)
            print(f"[VIEW OK] {name}")
        except Exception as ex:
            print(f"[VIEW FAIL] {name}: {type(ex).__name__}: {ex}")
            traceback.print_exc()
