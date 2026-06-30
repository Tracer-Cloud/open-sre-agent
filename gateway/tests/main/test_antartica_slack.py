'''
Description:
--------------------------------
We want to have a very specific tests that validates wether the agent is working or not.
The test goes like this:
- We start the gateway and get the agent
- We send a message to the agent: "send a message to slack with the temperature in antartica, compute the temperature first and then send the message"
- We expect the agent to produce two or three turns (1: create temperature, 2: send message via slack that includes the temperature)
'''
