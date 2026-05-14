# typed: false
# frozen_string_literal: true

# Copy this file to inxbit/homebrew-tap/Formula/pinghue.rb after the first
# PyPI sdist has been published. Then run:
#
#   brew update-python-resources Formula/pinghue.rb
#
# Replace the sdist SHA256 and generated resource blocks on every release.

class Pinghue < Formula
  include Language::Python::Virtualenv

  desc "Colored, concurrent ICMP/TCP ping monitor for the terminal"
  homepage "https://github.com/inxbit/pinghue"
  url "https://files.pythonhosted.org/packages/source/p/pinghue/pinghue-0.1.0.tar.gz"
  sha256 "REPLACE_WITH_SDIST_SHA256_FROM_PYPI"
  license "MIT"
  head "https://github.com/inxbit/pinghue.git", branch: "main"

  depends_on "python@3.12"

  resource "icmplib" do
    url "https://files.pythonhosted.org/packages/source/i/icmplib/icmplib-X.Y.Z.tar.gz"
    sha256 "REPLACE_VIA_UPDATE_PYTHON_RESOURCES"
  end

  resource "textual" do
    url "https://files.pythonhosted.org/packages/source/t/textual/textual-X.Y.Z.tar.gz"
    sha256 "REPLACE_VIA_UPDATE_PYTHON_RESOURCES"
  end

  def install
    virtualenv_install_with_resources
  end

  def caveats
    on_linux do
      <<~EOS
        ICMP mode on Linux needs one of:

          (A) Allow unprivileged ICMP for your group (recommended):
                sudo sysctl -w net.ipv4.ping_group_range="0 2147483647"
              Persist it:
                echo 'net.ipv4.ping_group_range=0 2147483647' \\
                  | sudo tee /etc/sysctl.d/99-pinghue.conf

          (B) Grant the binary CAP_NET_RAW (must be re-applied after every
              upgrade; Homebrew cannot do this for you):
                sudo setcap cap_net_raw+ep "$(brew --prefix)/opt/pinghue/libexec/bin/pinghue"

          (C) Skip ICMP entirely and use TCP mode:
                pinghue -p 443 example.com

        Run `pinghue --check` to diagnose your environment.
      EOS
    end
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/pinghue --version")
    assert_match "pinghue", shell_output("#{bin}/pinghue --help")
    system bin/"pinghue", "-p", "1", "127.0.0.1", "-c", "1", "--no-tui"
  end
end
