from fastapi import APIRouter, Body, Depends, HTTPException, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session

from backend import database, models, order_service as service
from backend.schemas import LoginRequest, RegisterRequest

router = APIRouter()


def credentials(payload, schema, **legacy):
    if payload is not None:
        return payload
    # Keep old query clients working; the UI uses JSON so passwords do not enter URLs.
    try:
        return schema(**legacy)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail="Uzupełnij poprawnie login, hasło i wymagane dane konta") from exc


@router.post("/register/", status_code=201, tags=["Konta"])
def register_user(payload: RegisterRequest | None = Body(default=None), username: str | None = None,
                  password: str | None = None, full_name: str | None = None,
                  db: Session = Depends(database.get_db)):
    data = credentials(payload, RegisterRequest, username=username, password=password, full_name=full_name)
    if db.query(models.User).filter(models.User.username == data.username).first():
        raise HTTPException(status_code=400, detail="Użytkownik już istnieje")
    user = models.User(username=data.username, full_name=data.full_name,
                       hashed_password=models.pwd_context.hash(data.password))
    db.add(user)
    service.commit(db)
    return {"message": "Użytkownik zarejestrowany"}


@router.post("/login/", tags=["Konta"])
def login_user(response: Response, payload: LoginRequest | None = Body(default=None),
               username: str | None = None, password: str | None = None,
               db: Session = Depends(database.get_db)):
    data = credentials(payload, LoginRequest, username=username, password=password)
    user = db.query(models.User).filter(models.User.username == data.username).first()
    if user is None or not models.pwd_context.verify(data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Nieprawidłowy login lub hasło")
    response.set_cookie(key="logged_in", value="true", httponly=True, samesite="lax")
    return {"message": "Zalogowano pomyślnie"}


@router.post("/logout/", tags=["Konta"])
def logout(response: Response):
    response.delete_cookie("logged_in")
    return {"message": "Wylogowano"}
