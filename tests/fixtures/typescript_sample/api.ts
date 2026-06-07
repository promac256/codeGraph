// Sample TypeScript file for parser tests

interface User {
  id: number;
  name: string;
  email: string;
}

type UserId = number;

class UserService {
  private users: Map<UserId, User> = new Map();

  constructor(private readonly baseUrl: string) {}

  async getUser(id: UserId): Promise<User | null> {
    // TODO: add caching
    const user = this.users.get(id);
    return user ?? null;
  }

  async createUser(data: Omit<User, 'id'>): Promise<User> {
    const id = Date.now();
    const user: User = { id, ...data };
    this.users.set(id, user);
    return user;
  }

  deleteUser(id: UserId): boolean {
    return this.users.delete(id);
  }
}

class AdminUserService extends UserService {
  async listAll(): Promise<User[]> {
    // FIXME: implement pagination
    return [];
  }
}

export { User, UserId, UserService, AdminUserService };
