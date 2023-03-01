import asyncio, re, traceback, psycopg
from fastapi import FastAPI, WebSocket, BackgroundTasks, Depends, Request, Form, status
from fastapi.responses import HTMLResponse

from starlette.responses import RedirectResponse
from starlette.templating import Jinja2Templates

from get_reviews import Reviews

from uvicorn.config import Config
from uvicorn.main import Server

conn = None #database connection. set in wsep()

templates = Jinja2Templates(directory="templates")

app = FastAPI()

r = None
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

@app.on_event('startup')
async def connect():
    global r
    global conn
    conn = await psycopg.AsyncConnection.connect('dbname=imdb user=dev password=dev host=localhost', autocommit=True)
    r = Reviews(conn)

@app.on_event('shutdown')
async def shutdown():
    global conn
    await conn.close()

@app.get('/')
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request}) #, "todo_list": todos})

@app.get('/')
async def get():
    return HTMLResponse(open('templates/index.html', 'r').read())

@app.post('/gather_reviews')
def gather_reviews(request: Request, movies: str = Form(...)):
    url = app.url_path_for('home')
    return {'success': movies.splitlines()}

async def send_recommendations(ws):
    try:
        async for data in r.get_recommendations():
            await ws.send_json(data)
    except Exception:
        traceback.print_exc()

@app.websocket('/ws')
async def wsep(ws: WebSocket):
    await ws.accept()
    await r.all_recommendations()
    loop.create_task(send_recommendations(ws)) #callback to stop when ws closes
    session_data = ''
    sent_ids = set()
    while True:
        data = await ws.receive_text()
        if data.startswith('ADD|') or data.startswith('RM|'):
            await r.add_watch(data)
        elif data.startswith('SEEN|'):
            await r.add_seen(data.split('|')[1])
        else:
            print('got ws message: ' + data)
            session_data += data + '\n'
            ids = [i for i in session_data.splitlines() if i not in sent_ids and re.match('tt[0-9]+', i)]
            if not len(ids):
                continue
            [sent_ids.add(i) for i in ids]
            print(sent_ids)
            await ws.send_text('FLAG|tableinit')
            async for res in r.get_infos(ids):
                if isinstance(res, str):
                    sent_ids.remove(res)
                    continue
                await ws.send_text('\t'.join(res))
            await ws.send_text('FLAG|rcount_update')
            async for msg in r.get_reviews(list(sent_ids)):
                print(str(msg))
                if isinstance(msg, list):
                    await ws.send_text('FLAG|err')
                    await ws.send_text(f'{msg[0]}|{r.errs[msg[1]]}')
                    await ws.send_text('FLAG|rcount_update')
                    continue
                elif msg.startswith('UUT|'): #update user table
                    await ws.send_text('FLAG|rate_table')
                    await ws.send_text(msg[4:])
                    print(f'user min reviews: {msg}')
                    await ws.send_text('FLAG|rcount_update')
                    continue
                await ws.send_text(msg)
            await ws.send_text('FLAG|reorg_rate_tab')
            await ws.send_text('trigger')
            await ws.send_text('FLAG|recommendations') #change prevFlag


config = Config(app=app, loop=loop, host='0.0.0.0', port=1215)
server = Server(config=config)
loop.run_until_complete(server.serve())