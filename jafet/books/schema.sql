create extension if not exists vector;

create table if not exists books(
    id serial primary key,
    mms_id text unique not null,
    title text,
    author text,
    isbn text,
    publisher text,
    place text,
    edition text,
    year int,
    language text,
    format text,
    subjects text[],
    call_number text,
    library text,
    location text,
    availability_status text,
    description text,
    embedding vector(3072),
    created_at timestamptz default now());
-- no ann index: 150 rows, exact scan is fine and 3072 dims is over pgvector's index limit
