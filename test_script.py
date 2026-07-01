import asyncio

from tests.utils.repl_driver import ReplDriver


async def main():
    async with ReplDriver() as repl:
        await repl.wait_for_prompt()
        await repl.send_input("/integrations show fake")
        print("Output for '/integrations show fake':")
        print(repl.get_output())

        await repl.send_input("/integrations fakecommand")
        print("Output for '/integrations fakecommand':")
        print(repl.get_output())

asyncio.run(main())
