# Discord Matcher Bot

Discord Matcher Bot is a Discord bot designed to help users connect by matching them based on preferences and profiles.

## Features

- **Profile Creation**: Users can create profiles specifying their age, gender, bio, and preferences.
- **Matching System**: The bot suggests potential matches based on compatibility criteria.
- **Interactive Swiping**: Users can swipe right or left on suggested profiles.
- **Mutual Matching**: When two users express interest in each other, the bot notifies both parties.

## Setup and Installation

### Prerequisites

- Docker
- Docker Compose
- Python 3.8 or higher

### Steps

1. **Clone the Repository**:
   ```sh
   git clone https://github.com/PhillipDiCarlo/discord-matcher.git
   cd discord-matcher
   ```

2. **Set Up Environment Variables**:
    - Create a `.env` file in the root directory and add the following variables:
      ```
      DISCORD_TOKEN=your_discord_bot_token
      ```

3. **Build and Run the Docker Container**:
   ```sh
   docker-compose up --build
   ```

   This command will build the Docker image and start the bot.

## Usage

Once the bot is running and added to your Discord server, users can interact with it using the following commands:

- **`/create_profile`**: Create a new profile.
- **`/update_profile`**: Update your existing profile.
- **`/delete_profile`**: Delete your profile.
- **`/start_matching`**: Begin the matching process and receive potential matches.
- **`/unmatch`**: Unmatch from your current match.

## Contributing

Contributions are welcome! Please fork the repository and create a pull request with your changes. Ensure that your code follows the project's coding standards and includes appropriate tests.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

