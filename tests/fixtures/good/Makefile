NAME = libftprintf.a
CC ?= cc
CFLAGS ?= -Wall -Wextra -Werror

all: $(NAME)

$(NAME): ft_printf.o
	ar rcs $@ $^

ft_printf.o: ft_printf.c
	$(CC) $(CFLAGS) -c $< -o $@

clean:
	rm -f ft_printf.o

fclean: clean
	rm -f $(NAME)

re: fclean all

.PHONY: all clean fclean re
