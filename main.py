import hashlib
import os
from typing import Annotated, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import create_engine, select
from sqlalchemy.exc import NoResultFound
from sqlalchemy.orm import aliased, sessionmaker
from sqlalchemy.orm.exc import UnmappedInstanceError

from database import database as models

uri = os.getenv("DATABASE_URL")
if uri.startswith("postgres://"):
    uri = uri.replace("postgres://", "postgresql://", 1)
engine = create_engine(uri)

Session = sessionmaker(bind=engine)
session = Session()

templates = Jinja2Templates(directory="templates")
app = FastAPI()

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")
# TODO: Update to use Routes
app.mount("/static", StaticFiles(directory="static"), name="static")


class Task(BaseModel):
    description: str
    status: models.Status
    id: Optional[int] = None


class User(BaseModel):
    username: str
    md5_password_hash: str
    email: str | None = None
    full_name: str | None = None
    disabled: bool | None = None


class UserCreate(BaseModel):
    username: str
    password: str
    email: str | None = None
    full_name: str | None = None


def generate_md5_hash(token):
    return hashlib.md5(token.encode("utf-8")).hexdigest()


def get_user_by_token(token: str):
    obj = aliased(models.User, name="obj")
    stmt = select(obj).where(obj.md5_password_hash == token)
    try:
        db_user = session.scalars(stmt).one()
    except NoResultFound:
        return
    return db_user


def get_current_user(token: Annotated[str, Depends(oauth2_scheme)]):
    user = get_user_by_token(token)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def get_all_todos():
    obj = aliased(models.Task, name="obj")
    stmt = select(obj)
    todos = [
        Task(id=i.id, description=i.description, status=i.status.value)
        for i in session.scalars(stmt)
    ]
    return todos


@app.post("/token")
def login(form_data: Annotated[OAuth2PasswordRequestForm, Depends()]):
    try:
        obj = aliased(models.User, name="obj")
        stmt = select(obj).where(obj.username == form_data.username)
        db_user = session.scalars(stmt).one()
    except NoResultFound:
        raise HTTPException(status_code=400, detail="Incorrect username or password")
    hashed_password = generate_md5_hash(form_data.password)
    if not hashed_password == db_user.md5_password_hash:
        raise HTTPException(status_code=400, detail="Incorrect username or password")

    return {"access_token": db_user.md5_password_hash, "token_type": "bearer"}


@app.get("/")
def root(request: Request):
    todos = get_all_todos()
    print(todos[0].status)
    return templates.TemplateResponse(
        "index.html", {"request": request, "todos": todos}
    )


@app.post("/tasks", status_code=201)
def create_task(task: Task):
    db_task = models.Task(**task.dict())
    session.add(db_task)
    session.commit()

    return {
        "id": db_task.id,
        "description": db_task.description,
        "status": db_task.status,
    }


@app.get("/tasks", status_code=200)
def get_tasks():
    todos = get_all_todos()

    return todos


@app.put("/tasks/{task_id}/status")
def update_task_status(task_id: int, status: models.Status):
    obj = aliased(models.Task, name="obj")
    db_task = session.execute(select(obj).filter_by(id=task_id)).scalar_one()
    db_task.status = status
    session.commit()

    return {
        "id": db_task.id,
        "description": db_task.description,
        "status": db_task.status,
    }


@app.put("/tasks/{task_id}")
def update_task(task_id: int, task: Task):
    obj = aliased(models.Task, name="obj")
    db_task = session.execute(select(obj).filter_by(id=task_id)).scalar_one()
    db_task.description = task.description
    db_task.status = task.status
    session.commit()

    return {
        "id": db_task.id,
        "description": db_task.description,
        "status": db_task.status,
    }


@app.get("/tasks/{task_id}")
def get_task(task_id, response: Response):
    obj = aliased(models.Task, name="obj")
    stmt = select(obj).where(obj.id == task_id)
    try:
        db_task = session.scalars(stmt).one()
    except NoResultFound:
        response.status_code = status.HTTP_404_NOT_FOUND
        return {}

    return {"description": db_task.description, "status": db_task.status}


@app.delete("/tasks/{task_id}")
def delete_task(task_id: int, response: Response):
    try:
        db_task = session.get(models.Task, task_id)
        session.delete(db_task)
        session.commit()
    except UnmappedInstanceError:
        response.status_code = status.HTTP_404_NOT_FOUND
        return {"error": f"could not delete task {task_id}"}

    return {"deleted": True}


@app.get("/user/me")
def get_user_me(current_user: Annotated[User, Depends(get_current_user)]):
    return current_user


@app.get("/user/admin")
def get_admin_user(current_user: Annotated[User, Depends(get_current_user)]):
    obj = aliased(models.User, name="obj")
    stmt = select(obj).where(obj.username == "admin")
    admin_user = session.scalars(stmt).one()
    if current_user.md5_password_hash == admin_user.md5_password_hash:
        return {"Success": "You accessed this endpoint!"}
    else:
        raise HTTPException(
            status_code=403, detail="This user cannot access this endpoint"
        )


@app.get("/user")
def get_users():
    obj = aliased(models.User, name="obj")
    stmt = select(obj)
    users = [
        User(
            id=i.id,
            username=i.username,
            md5_password_hash=i.md5_password_hash,
        )
        for i in session.scalars(stmt)
    ]

    return users


@app.get("/user/{username}")
def get_user(
    username: str,
    response: Response,
    token: Annotated[str, Depends(oauth2_scheme)],
):
    obj = aliased(models.User, name="obj")
    stmt = select(obj).where(obj.username == username)
    try:
        db_user = session.scalars(stmt).one()
    except NoResultFound:
        response.status_code = status.HTTP_404_NOT_FOUND
        return {}

    return {"id": db_user.id, "username": db_user.username}


@app.post("/user")
def create_user(user: UserCreate):
    obj = aliased(models.User, name="obj")
    stmt = select(obj).where(obj.username == user.username)
    try:
        db_user = session.scalars(stmt).one()
        raise HTTPException(
            status_code=409, detail=f"The user {user.username} already exists"
        )
    except NoResultFound:
        pass

    db_user = models.User(
        username=user.username,
        md5_password_hash=generate_md5_hash(user.password),
        email=user.email,
        full_name=user.full_name,
        disabled=False,
    )
    session.add(db_user)
    session.commit()

    return {
        "username": db_user.username,
        "pwd": db_user.md5_password_hash,
        "email": db_user.email,
    }


@app.delete("/user/{username}")
def delete_user(username: str):
    pass
