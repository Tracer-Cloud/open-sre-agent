
from tests.utils.repl_driver import ReplDriver


def test_repl_commands():
    with ReplDriver() as repl:
        repl.send("/integrations show fake", wait=3.0)
        print("Output for '/integrations show fake':")
        print(repl.text)

        repl.reset_output()
        repl.send("/integrations fakecommand", wait=3.0)
        print("Output for '/integrations fakecommand':")
        print(repl.text)
