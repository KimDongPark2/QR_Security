-- 개발·테스트용 SQLite 스키마. 테이블·열 이름은 SERVER_SPEC §4 약속 그대로.
-- 시연용 MySQL 스키마는 주승윤의 ../db/schema.sql.

CREATE TABLE drivers (
  driver_id     VARCHAR(10)  PRIMARY KEY,
  name          VARCHAR(20)  NOT NULL,
  password_hash VARCHAR(200) NOT NULL,
  role          VARCHAR(10)  NOT NULL CHECK (role IN ('HUB', 'DRIVER')),
  device_uuid   VARCHAR(64)  NULL
);

CREATE TABLE orders (
  id           INTEGER      PRIMARY KEY AUTOINCREMENT,
  order_no     VARCHAR(20)  NOT NULL UNIQUE,
  qr_token     VARCHAR(32)  NOT NULL UNIQUE,
  region       VARCHAR(50)  NOT NULL,
  status       VARCHAR(20)  NOT NULL DEFAULT 'READY'
               CHECK (status IN ('READY', 'AT_HUB', 'OUT_FOR_DELIVERY', 'DELIVERED')),
  created_at   DATETIME     NOT NULL,
  delivered_at DATETIME     NULL,
  photo_path   VARCHAR(200) NULL
);

-- 조회용 개인정보. *_enc 는 AES-256-GCM 암호문(base64).
CREATE TABLE recipient_pii (
  order_id    INTEGER     PRIMARY KEY REFERENCES orders(id),
  name_enc    TEXT        NOT NULL,
  phone_enc   TEXT        NOT NULL,
  address_enc TEXT        NOT NULL,
  safe_number VARCHAR(20) NOT NULL
);

-- 보존용. API로 복호화해서 보여주지 않는다.
CREATE TABLE pii_archive (
  id          INTEGER  PRIMARY KEY AUTOINCREMENT,
  order_id    INTEGER  NOT NULL,
  name_enc    TEXT     NOT NULL,
  phone_enc   TEXT     NOT NULL,
  address_enc TEXT     NOT NULL,
  archived_at DATETIME NOT NULL,
  keep_until  DATETIME NOT NULL
);

CREATE TABLE assignments (
  id        INTEGER     PRIMARY KEY AUTOINCREMENT,
  driver_id VARCHAR(10) NOT NULL REFERENCES drivers(driver_id),
  order_id  INTEGER     NOT NULL REFERENCES orders(id),
  work_date DATE        NOT NULL,
  UNIQUE (order_id, work_date)
);
CREATE INDEX idx_assignments_driver_date ON assignments(driver_id, work_date);

-- driver_id 에 FK 없음: 없는 ID로 로그인 시도한 것도 남긴다.
CREATE TABLE access_logs (
  id        INTEGER     PRIMARY KEY AUTOINCREMENT,
  driver_id VARCHAR(10) NOT NULL,
  order_id  INTEGER     NULL,
  action    VARCHAR(20) NOT NULL,
  result    VARCHAR(30) NOT NULL,
  at        DATETIME    NOT NULL
);

-- 배송 완료 트리거 (SERVER_SPEC §4 그대로). 시각은 KST로 맞추려고 +9 hours.
CREATE TRIGGER trg_orders_delivered
AFTER UPDATE OF status ON orders
WHEN NEW.status = 'DELIVERED' AND OLD.status <> 'DELIVERED'
BEGIN
  INSERT INTO pii_archive(order_id, name_enc, phone_enc, address_enc, archived_at, keep_until)
  SELECT order_id, name_enc, phone_enc, address_enc,
         datetime('now', '+9 hours'), datetime('now', '+9 hours', '+5 years')
  FROM recipient_pii WHERE order_id = NEW.id;
  DELETE FROM recipient_pii WHERE order_id = NEW.id;
END;
