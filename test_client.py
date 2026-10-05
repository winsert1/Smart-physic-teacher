from google import genai
client = genai.Client(api_key='test', http_options={'retry_options': {'attempts': 1}, 'timeout': 15})
print('Success')
